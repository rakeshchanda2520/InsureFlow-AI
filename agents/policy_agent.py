"""Policy Agent: is the policy valid, does it cover this loss, is anything excluded, are the documents complete."""
from andromeda.tools import register_tools, tool
from sqlalchemy import select

from agents import make_agent
from config import inr
from database import Document, claim_bundle, save_finding, session

AGENT = "policy_agent"

# A third-party-only policy pays for harm the insured causes to others, never for their own vehicle.
COVERED_LOSSES = {
    "Comprehensive": {"accident", "theft", "fire", "flood", "vandalism"},
    "Third-Party Only": set(),
}

EXCLUSIONS = {
    "drunk_driving": "Driving under the influence of alcohol or drugs",
    "invalid_licence": "Driver did not hold a valid driving licence",
    "commercial_use": "Private car used for hire or reward",
    "wear_and_tear": "Wear and tear, or mechanical/electrical breakdown",
    "intentional_damage": "Intentional or deliberate damage",
    "outside_territory": "Loss outside the geographical area covered",
}

_BASE_DOCS = ["claim_form", "rc_copy", "driving_licence", "policy_copy"]
REQUIRED_DOCS = {
    "accident": _BASE_DOCS + ["photos", "repair_estimate"],
    "theft": ["claim_form", "rc_copy", "policy_copy", "fir", "car_keys", "non_traceable_report"],
    "fire": _BASE_DOCS + ["photos", "repair_estimate", "fire_brigade_report"],
    "flood": _BASE_DOCS + ["photos", "repair_estimate"],
    "vandalism": _BASE_DOCS + ["photos", "repair_estimate", "fir"],
}


def _required(claim) -> list[str]:
    docs = list(REQUIRED_DOCS[claim.incident_type])
    if claim.third_party_involved and "fir" not in docs:
        docs.append("fir")  # injury or damage to others needs a police report
    return docs


@tool
def verify_policy(claim_id: str) -> dict:
    """Check the policy exists, belongs to the claimant, is ACTIVE and was in force on the incident date."""
    with session() as s:
        claim, policy, _ = claim_bundle(s, claim_id)
    problems = []
    if policy is None:
        problems.append(f"policy {claim.policy_id} does not exist")
    else:
        if policy.customer_id != claim.customer_id:
            problems.append(f"policy belongs to {policy.customer_id}, not the claimant {claim.customer_id}")
        if policy.status != "ACTIVE":
            problems.append(f"policy status is {policy.status}")
        if not policy.start_date <= claim.incident_date <= policy.end_date:
            problems.append(f"incident on {claim.incident_date} is outside the policy period "
                            f"{policy.start_date} to {policy.end_date}")
    summary = "Policy is valid on the incident date." if not problems else "Policy problem: " + "; ".join(problems) + "."
    save_finding(claim.claim_id, AGENT, "policy_verification", "FAIL" if problems else "PASS", summary, {"problems": problems})
    return {"valid": not problems, "problems": problems}


@tool
def check_coverage(claim_id: str) -> dict:
    """Check whether the policy type covers this kind of loss, and report the sum insured and add-ons."""
    with session() as s:
        claim, policy, _ = claim_bundle(s, claim_id)
    covered = policy is not None and claim.incident_type in COVERED_LOSSES.get(policy.policy_type, set())
    if covered:
        summary = (f"{policy.policy_type} policy covers {claim.incident_type}. Sum insured (IDV) {inr(policy.idv)}"
                   f"{', zero-depreciation add-on active' if policy.zero_dep else ''}.")
    else:
        kind = policy.policy_type if policy else "Unknown"
        summary = f"{kind} policy does not cover {claim.incident_type} damage to the insured's own vehicle."
    save_finding(claim.claim_id, AGENT, "coverage", "PASS" if covered else "FAIL", summary)
    return {"covered": covered, "summary": summary}


@tool
def check_exclusions(claim_id: str, narrative_flags: list[str] | None = None) -> dict:
    """Check the claim against the policy exclusions.

    The claim's declared facts (alcohol, licence) are checked automatically. If the incident description
    suggests an exclusion the form did not capture, pass its key in narrative_flags. Valid keys:
    drunk_driving, invalid_licence, commercial_use, wear_and_tear, intentional_damage, outside_territory.
    """
    unknown = [f for f in narrative_flags or [] if f not in EXCLUSIONS]
    if unknown:
        raise ValueError(f"Unknown exclusion keys {unknown}. Valid keys: {', '.join(EXCLUSIONS)}")
    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)

    triggered = set(narrative_flags or [])
    if claim.alcohol_involved:
        triggered.add("drunk_driving")
    if not claim.driver_licence_valid:
        triggered.add("invalid_licence")
    reasons = [EXCLUSIONS[k] for k in sorted(triggered)]
    summary = "Exclusion applies: " + "; ".join(reasons) + "." if reasons else "No policy exclusion applies."
    save_finding(claim.claim_id, AGENT, "exclusions", "FAIL" if reasons else "PASS", summary, {"triggered": sorted(triggered)})
    return {"excluded": bool(reasons), "reasons": reasons, "claimant_statement": claim.description}


@tool
def get_required_documents(claim_id: str) -> dict:
    """List the documents required for this claim's incident type."""
    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
    return {"incident_type": claim.incident_type, "required": _required(claim)}


@tool
def validate_documents(claim_id: str) -> dict:
    """Compare the documents received with those required and report which are missing."""
    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
        received = set(s.scalars(select(Document.doc_type).where(Document.claim_id == claim.claim_id)))
    missing = [d for d in _required(claim) if d not in received]
    summary = "All required documents received." if not missing else "Missing documents: " + ", ".join(missing) + "."
    save_finding(claim.claim_id, AGENT, "documents", "WARN" if missing else "PASS", summary, {"missing": missing})
    return {"complete": not missing, "missing": missing}


register_tools([verify_policy, check_coverage, check_exclusions, get_required_documents, validate_documents])


def build():
    return make_agent(
        AGENT, "You are the Policy Agent of a motor-insurance claims team. You decide whether a registered claim "
               "falls inside the policy: validity, coverage, exclusions and documents.", "policy-verification")
