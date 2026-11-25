"""The Supervisor that coordinates the four agents, and the human-review step that closes a claim."""
from config import MODEL, ROOT, inr  # first: loads .env before Andromeda is imported

from andromeda import HumanMessage
from andromeda.config import MiddlewareConfig, SupervisorConfig
from andromeda.config.config import CompliancePatternsConfig, DataPatternsConfig
from andromeda.core.middleware.skills import SkillsMiddleware
from andromeda.core.supervisor import Supervisor

from agents import assessment_agent, claim_agent, fraud_agent, policy_agent
from database import (Customer, claim_bundle, claim_for_submission, get_findings, remember, save_finding, session,
                      stage_submission)
from schemas import ClaimRequest, ClaimResult

BLOCKED = "This claim was blocked by the safety guardrails and needs manual handling."
_NEVER = r"(?!)"  # a pattern that matches nothing

# The default data patterns block any text holding a phone number or a long digit run, and the default
# compliance list bans the phrase "no exclusions", which the policy check legitimately produces.
GUARDRAILS = MiddlewareConfig.GuardrailOptions(
    input=True,
    output=True,
    data_patterns=DataPatternsConfig(email=_NEVER, ssn=_NEVER, phone=_NEVER, credit_card=_NEVER),
    compliance_patterns=CompliancePatternsConfig(patterns=[
        r"\bguaranteed\s+(approval|payout|settlement)\b", r"\bcannot\s+be\s+denied\b", r"\bwill\s+be\s+(approved|paid)\b",
    ]),
    blocked_message=BLOCKED,
)

PROMPT = """You run motor-insurance claim processing for InsureFlow AI. Load the claim-orchestration skill
first and follow it exactly. You hold no claim data yourself: every fact comes from your specialists.

- claim_agent: finds the customer and policy and registers the claim (returns the claim_id).
- policy_agent: policy validity, coverage, exclusions and required documents.
- fraud_agent: duplicate claims, claim history and the fraud score.
- assessment_agent: damage estimate, depreciation, deductible, payout and the recommendation.

The specialists unlock their own tools by loading their skills; you never call those tools."""

def build_supervisor() -> Supervisor:
    """A fresh team per claim: agent memory is instance-level, so reusing one would leak a claim into the next."""
    skills = SkillsMiddleware(backend="filesystem", repo_root=ROOT, sources=["/skills/claim-orchestration"])
    config = SupervisorConfig(
        name="supervisor", model=MODEL, prompt=PROMPT, enable_planning=True, allowed_route_types=["task"],
        recursion_limit=200,  # each loop crosses several middleware nodes
        middleware=MiddlewareConfig(enabled=True, tool_error_handler=True, guardrails=GUARDRAILS, custom=[skills]),
    )
    agents = [claim_agent.build(), policy_agent.build(), fraud_agent.build(), assessment_agent.build()]
    return Supervisor(agents=agents, config=config)


def process_claim(req: ClaimRequest) -> ClaimResult:
    """Run one submission through the supervisor. Ends with a recommendation awaiting a human."""
    submission_id = stage_submission(req)
    # The claimant's own words are included so the input guardrails scan them; the rest is staged in SQLite.
    request = (f"New motor claim submission {submission_id}. Customer {req.customer_id}, policy {req.policy_id}. "
               f"Incident: {req.incident_type} on {req.incident_date} at {req.location}. "
               f"Claimant statement: {req.description}")
    # The plan is left empty on purpose: supervise() appends todo reminders as later human messages, and the
    # input guardrail only scans the latest one, so a seeded plan would hide the claimant's text from it.
    state = {"messages": [HumanMessage(content=request)], "plan": []}
    reply = str(build_supervisor().supervise(state)["messages"][-1].content)

    claim_id = claim_for_submission(submission_id)
    if claim_id is None:
        return ClaimResult(claim_id=None, status="BLOCKED" if reply == BLOCKED else "NOT_REGISTERED", summary=reply)

    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
    # If the output guardrail replaced the reply, the recorded decision is still the source of truth.
    decision = next((f for f in get_findings(claim_id) if f.name == "decision"), None)
    summary = decision.summary + " " + " ".join(decision.data["reasons"]) if reply == BLOCKED and decision else reply
    return ClaimResult(claim_id=claim_id, status=claim.status, recommendation=claim.recommendation,
                       payout=claim.payout, summary=summary)


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
    return claim.notification
