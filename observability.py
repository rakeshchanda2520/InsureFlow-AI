"""Langfuse tracing and evaluation, wired once and shared by every agent.

Tracing needs no code: with LANGFUSE_* set (config.py loads .env before Andromeda is imported), Andromeda
traces every agent, model and tool call. This module adds what it cannot know on its own - whose claim a
trace is, what the application did around the agents - and the evaluators that judge each agent.
With Langfuse unconfigured, all of it does nothing.
"""
import os
import re

from andromeda.core.workflow import LangfuseEvaluator, hallucination, tool_usage

from config import MODEL
from database import Claim, session

ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
TEAM = {"claim_agent", "policy_agent", "fraud_agent", "assessment_agent"}
OUTCOME = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "INVESTIGATE": "INVESTIGATION"}

_AMOUNT = re.compile(r"₹\s?[\d,]+")
_CLAIM_ID = re.compile(r"CLM-\d{4}-\d{4}")


# ---- evaluators: deterministic checks of the behaviour this design depends on -------------------------------
# Each receives the run's trajectory (messages, tools_called, tool_results, output) and returns a value and a comment.

def _tool_calls(inp) -> list[dict]:
    return inp.trajectory.get("tools_called") or []


def _skill_first(inp):
    """Tools are unlocked by a skill, so the first thing an agent does must be to load it."""
    names = [call.get("name") for call in _tool_calls(inp)]
    return (names[0] == "load_skill", f"first tool was {names[0]}") if names else None


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
    """The supervisor should have routed to all four specialists."""
    routed = {(call.get("args") or {}).get("agent_name") for call in _tool_calls(inp) if call.get("name") == "route_to_agent"}
    missing = sorted(TEAM - routed)
    return 1 - len(missing) / len(TEAM), "all four agents ran" if not missing else "not run: " + ", ".join(missing)


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


SKILL_FIRST = LangfuseEvaluator(name="skill_first", eval_fn=_skill_first, data_type="BOOLEAN")
GROUNDED = LangfuseEvaluator(name="grounded", eval_fn=_grounded, data_type="NUMERIC")
PIPELINE_COMPLETE = LangfuseEvaluator(name="pipeline_complete", eval_fn=_pipeline_complete, data_type="NUMERIC")
ANSWER_MATCHES_DECISION = LangfuseEvaluator(name="answer_matches_decision", eval_fn=_answer_matches_decision, data_type="BOOLEAN")

# An LLM judge for what rules cannot see: a summary that drifts from its own tool results. One call per agent run;
# set INSUREFLOW_JUDGE_SAMPLE below 1 to judge only a share of runs.
JUDGE = hallucination(sample_rate=float(os.getenv("INSUREFLOW_JUDGE_SAMPLE", "1")))

SPECIALIST = [tool_usage(), SKILL_FIRST, GROUNDED, JUDGE]
SUPERVISOR = SPECIALIST + [PIPELINE_COMPLETE, ANSWER_MATCHES_DECISION]


# ---- wiring ---------------------------------------------------------------------------------------------------

def observe(agent, evaluators: list):
    """Attach evaluators to an agent. Scores land on the claim's trace as '<agent name>:<evaluator>'."""
    if ENABLED:
        agent.with_evaluators(
            evaluators, scheduler={"max_workers": 2, "max_pending": 64},
            model={"name": MODEL.name, "provider": MODEL.provider, "temperature": 0.0})
    return agent


def run_config(customer_id: str, submission_id: str, source: str) -> dict:
    """thread_id and trace attributes for one claim. One thread_id is one trace, so every agent's work on
    the claim lands together. Only ids go in: the claimant's statement never belongs in trace metadata."""
    return {"thread_id": submission_id, "metadata": {"langfuse_attributes": {
        "user_id": customer_id, "session_id": submission_id, "trace_name": "claim-processing",
        "environment": os.getenv("INSUREFLOW_ENV", "development"), "tags": ["insureflow", source]}}}


def _trace_context(submission_id: str) -> dict:
    from langfuse import Langfuse
    return {"trace_id": Langfuse.create_trace_id(seed=submission_id)}  # the id Andromeda derives from thread_id


def event(name: str, submission_id: str, level: str = "DEFAULT", **metadata) -> None:
    """Record something the application did on the claim's trace: a guardrail block, the human's decision."""
    if ENABLED and submission_id:
        from langfuse import get_client
        get_client().create_event(trace_context=_trace_context(submission_id), name=name, level=level, metadata=metadata)


def score_trace(submission_id: str, name: str, ok: bool, comment: str) -> None:
    """Add a pass/fail score to a claim's trace after the run: the reviewer's verdict, or an eval result."""
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
