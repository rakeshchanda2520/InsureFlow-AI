"""Langfuse tracing and evaluation, wired once and shared by every agent.

Tracing needs no code: with LANGFUSE_* set (config.py loads .env before Andromeda is imported), Andromeda
traces every agent, model and tool call. This module adds what it cannot know on its own - whose claim a
trace is, what the application did around the agents - and the evaluators that judge each agent.
With Langfuse unconfigured, all of it does nothing.
"""
import os
import re

from andromeda.core.workflow import LangfuseEvaluator, hallucination, relevance, tool_usage

from config import MODEL
from database import Claim, session

ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
TEAM = ["claim_agent", "policy_agent", "fraud_agent", "assessment_agent"]  # the order the supervisor must route in
OUTCOME = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "INVESTIGATE": "INVESTIGATION"}
RECOMMENDATIONS = tuple(OUTCOME)

# What each specialist must call, in order: the procedure its skill states. Keep in step with skills/*/SKILL.md.
STEPS = {
    "claim_agent": ["load_skill", "get_customer", "get_policy", "create_claim"],
    "policy_agent": ["load_skill", "verify_policy", "check_coverage", "check_exclusions", "get_required_documents", "validate_documents"],
    "fraud_agent": ["load_skill", "get_claim_history", "detect_duplicate", "check_claim_frequency", "calculate_fraud_score", "generate_fraud_report"],
    "assessment_agent": ["load_skill", "estimate_damage", "calculate_depreciation", "calculate_deductible", "calculate_payout", "generate_decision"],
}
ACTION_BUDGET = {"supervisor": 20}  # tool calls per run; any specialist gets 12
SPECIALIST_BUDGET = 12

_AMOUNT = re.compile(r"₹\s?[\d,]+")
_CLAIM_ID = re.compile(r"CLM-\d{4}-\d{4}")


# ---- evaluators: deterministic checks, free and exact ---------------------------------------------------------
# Each receives the run's trajectory (messages, tools_called, tool_results, input, output) and returns a value and a comment.

def _tool_calls(inp) -> list[dict]:
    return inp.trajectory.get("tools_called") or []


def _skill_first(inp):
    """Tools are unlocked by a skill, so the first thing an agent does must be to load it."""
    names = [call.get("name") for call in _tool_calls(inp)]
    return (names[0] == "load_skill", f"first tool was {names[0]}") if names else None


def _tool_trajectory(inp):
    """Tool-call accuracy: F1 of the tools called against the tools its skill requires, halved if they ran out of order."""
    expected = STEPS.get(inp.step_name)
    called = [call.get("name") for call in _tool_calls(inp)]
    if not expected or not called:
        return None
    used = list(dict.fromkeys(called))
    precision = len([t for t in used if t in expected]) / len(used)
    recall = len([t for t in expected if t in used]) / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    in_order = [t for t in used if t in expected] == [t for t in expected if t in used]
    unexpected = [t for t in used if t not in expected]
    note = f"F1 {f1:.2f}" + ("" if in_order else ", out of order") + (f", unexpected: {', '.join(unexpected)}" if unexpected else "")
    return f1 if in_order else f1 / 2, note


def _consistent_ids(inp):
    """Argument correctness: every claim_id a tool is called with is one single id, and it is the one the agent was given."""
    ids = {(call.get("args") or {}).get("claim_id") for call in _tool_calls(inp)} - {None}
    if not ids:
        return None
    given = str(inp.trajectory.get("input") or "")
    ok = len(ids) == 1 and next(iter(ids)) in given
    return ok, "one claim id, as given" if ok else f"ids used: {sorted(ids)}"


def _within_budget(inp):
    """An action budget: a run that needs far more tool calls than its procedure has steps is looping."""
    limit = ACTION_BUDGET.get(inp.step_name, SPECIALIST_BUDGET)
    calls = len(_tool_calls(inp))
    return calls <= limit, f"{calls} tool calls against a budget of {limit}"


def _grounded(inp):
    """Every rupee amount and claim id a reply states must appear in what the agent's tools returned."""
    reply = inp.trajectory.get("output") or ""
    evidence = " ".join(str(r.get("content")) for r in inp.trajectory.get("tool_results") or [])
    known = {n.replace(",", "") for n in re.findall(r"[\d,]+", evidence)} | set(_CLAIM_ID.findall(evidence))
    stated = {re.sub(r"\D", "", a) for a in _AMOUNT.findall(reply)} | set(_CLAIM_ID.findall(reply))
    if not stated:
        return None
    invented = sorted(stated - known)
    return 1 - len(invented) / len(stated), "every figure comes from a tool result" if not invented else "in no tool result: " + ", ".join(invented)


def _pipeline_complete(inp):
    """The supervisor should have routed to all four specialists, in the order the procedure sets."""
    routed = list(dict.fromkeys(
        (call.get("args") or {}).get("agent_name") for call in _tool_calls(inp) if call.get("name") == "route_to_agent"))
    missing = [a for a in TEAM if a not in routed]
    in_order = [a for a in routed if a in TEAM] == [a for a in TEAM if a in routed]
    score = (1 - len(missing) / len(TEAM)) * (1 if in_order else 0.5)
    return score, ("all four agents ran" if not missing else "not run: " + ", ".join(missing)) + ("" if in_order else "; out of order")


def _answer_matches_decision(inp):
    """The supervisor's answer must state the recommendation that was actually recorded for the claim."""
    claim_ids = _CLAIM_ID.findall(inp.trajectory.get("output") or "")
    if not claim_ids:
        return None
    with session() as s:
        claim = s.get(Claim, claim_ids[0])
    if claim is None or not claim.recommendation:
        return None
    return claim.recommendation in inp.trajectory["output"].upper(), f"recorded recommendation: {claim.recommendation}"


def _final_answer_format(inp):
    """The supervisor's answer must follow the form its skill sets: claim, recommendation, payout, at most five lines."""
    reply = inp.trajectory.get("output") or ""
    if not _CLAIM_ID.search(reply):
        return None  # an unregistered claim has no such answer; the other checks cover it
    problems = []
    if not any(r in reply.upper() for r in RECOMMENDATIONS):
        problems.append("no recommendation")
    if not _AMOUNT.search(reply):
        problems.append("no payout amount")
    if len([line for line in reply.splitlines() if line.strip()]) > 5:
        problems.append("longer than five lines")
    return not problems, "follows the required form" if not problems else "; ".join(problems)


SKILL_FIRST = LangfuseEvaluator(name="skill_first", eval_fn=_skill_first, data_type="BOOLEAN")
TOOL_TRAJECTORY = LangfuseEvaluator(name="tool_trajectory", eval_fn=_tool_trajectory, data_type="NUMERIC")
CONSISTENT_IDS = LangfuseEvaluator(name="consistent_ids", eval_fn=_consistent_ids, data_type="BOOLEAN")
WITHIN_BUDGET = LangfuseEvaluator(name="within_budget", eval_fn=_within_budget, data_type="BOOLEAN")
GROUNDED = LangfuseEvaluator(name="grounded", eval_fn=_grounded, data_type="NUMERIC")
PIPELINE_COMPLETE = LangfuseEvaluator(name="pipeline_complete", eval_fn=_pipeline_complete, data_type="NUMERIC")
ANSWER_MATCHES_DECISION = LangfuseEvaluator(name="answer_matches_decision", eval_fn=_answer_matches_decision, data_type="BOOLEAN")
FINAL_ANSWER_FORMAT = LangfuseEvaluator(name="final_answer_format", eval_fn=_final_answer_format, data_type="BOOLEAN")

# ---- LLM judges, for what rules cannot see: a summary that drifts from its tool results, an answer that misses the
# point. A judge costs a model call per run, so INSUREFLOW_JUDGE_SAMPLE can judge only a share. They run on a model of
# their own (INSUREFLOW_JUDGE_MODEL): a model tends to rate its own writing generously.
JUDGE_SAMPLE = float(os.getenv("INSUREFLOW_JUDGE_SAMPLE", "1"))
JUDGE_MODEL = os.getenv("INSUREFLOW_JUDGE_MODEL", MODEL.name)
HALLUCINATION = hallucination(sample_rate=JUDGE_SAMPLE)
RELEVANCE = relevance(sample_rate=JUDGE_SAMPLE)

SPECIALIST = [tool_usage(), SKILL_FIRST, TOOL_TRAJECTORY, CONSISTENT_IDS, WITHIN_BUDGET, GROUNDED, HALLUCINATION]
SUPERVISOR = [tool_usage(), SKILL_FIRST, WITHIN_BUDGET, GROUNDED, HALLUCINATION,
              PIPELINE_COMPLETE, ANSWER_MATCHES_DECISION, FINAL_ANSWER_FORMAT, RELEVANCE]


# ---- wiring ---------------------------------------------------------------------------------------------------

def observe(agent, evaluators: list):
    """Attach evaluators to an agent. Scores land on the claim's trace as '<agent name>:<evaluator>'."""
    if ENABLED:
        agent.with_evaluators(
            evaluators, scheduler={"max_workers": 2, "max_pending": 64},
            model={"name": JUDGE_MODEL, "provider": MODEL.provider, "temperature": 0.0})
    return agent


def run_config(customer_id: str, submission_id: str, source: str) -> dict:
    """thread_id and trace attributes for one claim. One thread_id is one trace, so every agent's work on
    the claim lands together. Only ids go in: the claimant's statement never belongs in trace metadata."""
    return {"thread_id": submission_id, "metadata": {"langfuse_attributes": {
        "user_id": customer_id, "session_id": submission_id, "trace_name": "claim-processing",
        "environment": os.getenv("INSUREFLOW_ENV", "development"), "tags": ["insureflow", source, MODEL.name]}}}


def _trace_context(submission_id: str) -> dict:
    from langfuse import Langfuse
    return {"trace_id": Langfuse.create_trace_id(seed=submission_id)}  # the id Andromeda derives from thread_id


def event(name: str, submission_id: str, level: str = "DEFAULT", **metadata) -> None:
    """Record something the application did on the claim's trace: a guardrail block, the human's decision."""
    if ENABLED and submission_id:
        from langfuse import get_client
        get_client().create_event(trace_context=_trace_context(submission_id), name=name, level=level, metadata=metadata)


def score_trace(submission_id: str, name: str, ok: bool, comment: str) -> None:
    """Add a pass/fail score to a claim's trace after the run: the reviewer's verdict, an eval result, a budget."""
    if ENABLED and submission_id:
        from langfuse import get_client
        get_client().create_score(trace_id=_trace_context(submission_id)["trace_id"], name=name,
                                  value=float(ok), data_type="BOOLEAN", comment=comment)


def flush() -> None:
    """A CLI exits quickly: let background scoring and trace export finish first."""
    if ENABLED:
        from andromeda.core.workflow import wait_for_evaluations
        from langfuse import get_client
        wait_for_evaluations()
        get_client().flush()
