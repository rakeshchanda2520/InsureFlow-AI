"""The Supervisor that coordinates the four agents, and the human-review step that closes a claim."""
import time

from config import LATENCY_BUDGET_S, MODEL, ROOT, inr  # first: loads .env before Andromeda is imported

from andromeda import HumanMessage
from andromeda.config import SupervisorConfig
from andromeda.core.middleware.skills import SkillsMiddleware
from andromeda.core.supervisor import Supervisor

from agents import assessment_agent, claim_agent, fraud_agent, policy_agent
from database import (Customer, claim_bundle, claim_for_submission, get_findings, remember, save_finding, session,
                      set_outcome, stage_submission)
from observability import OUTCOME, SUPERVISOR, event, observe, run_config, score_trace
from schemas import ClaimRequest, ClaimResult
from security import BLOCKED, SANDWICH, SUPERVISOR_RULES, supervisor_middleware, untrusted

PROMPT = f"""You run motor-insurance claim processing for InsureFlow AI. Load the claim-orchestration skill
first and follow it exactly. You hold no claim data yourself: every fact comes from your specialists.

- claim_agent: finds the customer and policy and registers the claim (returns the claim_id).
- policy_agent: policy validity, coverage, exclusions and required documents.
- fraud_agent: duplicate claims, claim history and the fraud score.
- assessment_agent: damage estimate, depreciation, deductible, payout and the recommendation.

The specialists unlock their own tools by loading their skills; you never call those tools.

{SUPERVISOR_RULES}"""


def build_supervisor() -> Supervisor:
    """A fresh team per claim: agent memory is instance-level, so reusing one would leak a claim into the next."""
    skills = SkillsMiddleware(backend="filesystem", repo_root=ROOT, sources=["/skills/claim-orchestration"])
    config = SupervisorConfig(
        name="supervisor", model=MODEL, prompt=PROMPT, enable_planning=True, allowed_route_types=["task"],
        recursion_limit=200,  # each loop crosses several middleware nodes
        middleware=supervisor_middleware(skills),
    )
    agents = [claim_agent.build(), policy_agent.build(), fraud_agent.build(), assessment_agent.build()]
    return observe(Supervisor(agents=agents, config=config), SUPERVISOR)


def process_claim(req: ClaimRequest, source: str = "manual") -> ClaimResult:
    """Run one submission through the supervisor. Ends with a recommendation awaiting a human."""
    submission_id = stage_submission(req)
    # Everything a customer typed goes between tags, so the model and the input guardrails can tell it from instructions.
    request = (f'<submission id="{submission_id}" customer="{req.customer_id}" policy="{req.policy_id}" '
               f'type="{req.incident_type}" date="{req.incident_date}">\n'
               f"<location>{untrusted(req.location)}</location>\n"
               f"<claimant_statement>\n{untrusted(req.description)}\n</claimant_statement>\n</submission>\n{SANDWICH}")
    # The plan is left empty on purpose: supervise() appends todo reminders as later human messages, and the
    # input guardrail only scans the latest one, so a seeded plan would hide the claimant's text from it.
    state = {"messages": [HumanMessage(content=request)], "plan": []}
    started = time.perf_counter()
    reply = str(build_supervisor().supervise(state, **run_config(req.customer_id, submission_id, source))["messages"][-1].content)
    latency = time.perf_counter() - started
    score_trace(submission_id, "latency_budget", latency <= LATENCY_BUDGET_S, f"{latency:.0f}s against a {LATENCY_BUDGET_S:.0f}s budget")

    claim_id = claim_for_submission(submission_id)
    if reply == BLOCKED:
        event("guardrail-blocked", submission_id, level="WARNING", claim_registered=claim_id is not None)
    if claim_id is None:
        status = "BLOCKED" if reply == BLOCKED else "NOT_REGISTERED"
        set_outcome(submission_id, status)
        return ClaimResult(submission_id=submission_id, claim_id=None, status=status, summary=reply, latency_s=latency)

    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
    set_outcome(submission_id, claim.status)
    if claim.status != "PENDING_REVIEW":
        event("pipeline-incomplete", submission_id, level="ERROR", status=claim.status)
    # If the output guardrail replaced the reply, the recorded decision is still the source of truth.
    decision = next((f for f in get_findings(claim_id) if f.name == "decision"), None)
    summary = decision.summary + " " + " ".join(decision.data["reasons"]) if reply == BLOCKED and decision else reply
    return ClaimResult(submission_id=submission_id, claim_id=claim_id, status=claim.status, recommendation=claim.recommendation,
                       payout=claim.payout, summary=summary, latency_s=latency)


def finalize_claim(claim_id: str, decision: str, note: str = "") -> str:
    """Record the human reviewer's decision, store memory for the customer's next claim, return the notification."""
    decision_row = next((f for f in get_findings(claim_id) if f.name == "decision"), None)
    estimated = decision_row.data["breakdown"]["payout"] if decision_row else 0
    reasons = " ".join(decision_row.data["reasons"]) if decision_row else ""

    with session() as s:
        claim, _, vehicle = claim_bundle(s, claim_id)
        customer = s.get(Customer, claim.customer_id)
        claim.status = claim.reviewer_decision = decision
        claim.reviewer_note = note
        claim.payout = estimated if decision == "APPROVED" else 0 if decision == "REJECTED" else claim.payout
        claim.notification = {
            "APPROVED": f"Dear {customer.name}, your claim {claim_id} for your {vehicle.make} {vehicle.model} has been approved. "
                        f"The settlement of {inr(claim.payout)} will be credited to your registered account within 7 working days.",
            "REJECTED": f"Dear {customer.name}, after review we are unable to approve claim {claim_id}. {note or reasons} "
                        "You may contact us to appeal this decision.",
            "INVESTIGATION": f"Dear {customer.name}, claim {claim_id} needs further review. "
                             "Our team will contact you shortly for additional information.",
        }[decision]
        s.commit()

    save_finding(claim_id, "human_review", "review", "INFO", f"{decision} by the human reviewer. {note}".strip())
    remember(claim.customer_id, claim_id, "supervisor",
             f"{claim.incident_type.capitalize()} claim {decision.lower()}, payout {inr(claim.payout)}. {note}".strip())
    agreed = OUTCOME.get(claim.recommendation) == decision
    event("human-review", claim.submission_id, decision=decision, agents_said=claim.recommendation, agreed=agreed)
    score_trace(claim.submission_id, "human_agreement", agreed, f"agents: {claim.recommendation}, reviewer: {decision}")
    return claim.notification
