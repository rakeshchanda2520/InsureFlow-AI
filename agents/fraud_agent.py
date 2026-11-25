"""Fraud Agent: duplicate claims, claim history, and a transparent fraud score."""
from datetime import timedelta

from andromeda.tools import register_tools, tool
from sqlalchemy import select

from agents import make_agent
from config import (DUPLICATE_WINDOW_DAYS, EARLY_CLAIM_DAYS, FRAUD_HIGH, FRAUD_MEDIUM, FREQUENCY_LIMIT,
                    FREQUENCY_WINDOW_DAYS, LATE_REPORT_DAYS, inr)
from database import Claim, Policy, claim_bundle, remember, save_finding, session

AGENT = "fraud_agent"


def _row(c: Claim) -> dict:
    return {"claim_id": c.claim_id, "incident_date": str(c.incident_date), "type": c.incident_type,
            "amount": inr(c.estimated_damage), "status": c.status}


def _signals(claim_id: str) -> dict:
    """Everything the fraud checks look at, read straight from the database."""
    with session() as s:
        claim, policy, _ = claim_bundle(s, claim_id)
        others = list(s.scalars(select(Claim).where(Claim.claim_id != claim.claim_id)))
        vehicle_policies = set(s.scalars(select(Policy.policy_id).where(Policy.vehicle_id == policy.vehicle_id))) if policy else set()

    gap = timedelta(days=DUPLICATE_WINDOW_DAYS)
    window_start = claim.incident_date - timedelta(days=FREQUENCY_WINDOW_DAYS)
    return {
        "claim": claim,
        "duplicates": [c for c in others if abs(c.incident_date - claim.incident_date) <= gap
                       and (c.policy_id in vehicle_policies or c.customer_id == claim.customer_id)],
        "prior": [c for c in others if c.customer_id == claim.customer_id and window_start <= c.incident_date < claim.incident_date],
        "late_days": (claim.reported_date - claim.incident_date).days,
        "policy_age_days": (claim.incident_date - policy.start_date).days if policy else None,
        "claimed_vs_idv": claim.estimated_damage / policy.idv if policy else 0,
    }


def _score(sig: dict) -> tuple[int, str, list[dict]]:
    found = []
    if sig["duplicates"]:
        found.append((40, "possible duplicate of " + ", ".join(c.claim_id for c in sig["duplicates"])))
    n = len(sig["prior"])
    if n >= FREQUENCY_LIMIT:
        found.append((25, f"{n} earlier claims in the last {FREQUENCY_WINDOW_DAYS} days"))
    elif n == FREQUENCY_LIMIT - 1:
        found.append((10, f"{n} earlier claims in the last {FREQUENCY_WINDOW_DAYS} days"))
    if sig["late_days"] > LATE_REPORT_DAYS:
        found.append((15, f"reported {sig['late_days']} days after the incident"))
    age = sig["policy_age_days"]
    if age is not None and 0 <= age < EARLY_CLAIM_DAYS:
        found.append((20, f"claim made {age} days after the policy started"))
    if sig["claimed_vs_idv"] > 0.5:
        found.append((10, f"claimed amount is {sig['claimed_vs_idv']:.0%} of the insured value"))

    score = min(100, sum(points for points, _ in found))
    level = "high" if score >= FRAUD_HIGH else "medium" if score >= FRAUD_MEDIUM else "low"
    return score, level, [{"points": p, "indicator": text} for p, text in found]


@tool
def get_claim_history(customer_id: str) -> list[dict]:
    """List all earlier claims by a customer, newest first."""
    with session() as s:
        rows = s.scalars(select(Claim).where(Claim.customer_id == customer_id.strip().upper()).order_by(Claim.incident_date.desc()))
        return [_row(c) for c in rows]


@tool
def detect_duplicate(claim_id: str) -> dict:
    """Look for another claim on the same vehicle or by the same customer within a few days of this incident."""
    sig = _signals(claim_id)
    dupes = [_row(c) for c in sig["duplicates"]]
    summary = ("Possible duplicate of " + ", ".join(d["claim_id"] for d in dupes) + "."
               if dupes else "No duplicate claim found.")
    save_finding(sig["claim"].claim_id, AGENT, "duplicate_check", "FAIL" if dupes else "PASS", summary, {"duplicates": dupes})
    return {"duplicate_found": bool(dupes), "matches": dupes}


@tool
def check_claim_frequency(claim_id: str) -> dict:
    """Count the customer's earlier claims in the last 12 months."""
    sig = _signals(claim_id)
    n = len(sig["prior"])
    flagged = n >= FREQUENCY_LIMIT
    summary = f"{n} earlier claim(s) in the last {FREQUENCY_WINDOW_DAYS} days" + (" - above the usual frequency." if flagged else ".")
    save_finding(sig["claim"].claim_id, AGENT, "frequency_check", "WARN" if flagged else "PASS", summary,
                 {"claims": [_row(c) for c in sig["prior"]]})
    return {"earlier_claims": n, "flagged": flagged}


@tool
def calculate_fraud_score(claim_id: str) -> dict:
    """Calculate a 0-100 fraud risk score from duplicates, frequency, reporting delay, policy age and claim size."""
    sig = _signals(claim_id)
    score, level, indicators = _score(sig)
    return {"score": score, "level": level, "indicators": indicators,
            "claimant_statement": sig["claim"].description, "damaged_parts": sig["claim"].damaged_parts}


@tool
def generate_fraud_report(claim_id: str, analyst_note: str = "") -> dict:
    """Produce the final fraud report. analyst_note is your own short reading of the claimant's description."""
    sig = _signals(claim_id)
    claim = sig["claim"]
    score, level, indicators = _score(sig)
    action = {"low": "No fraud indicators that need action.",
              "medium": "Enhanced review before any payment.",
              "high": "Refer to the special investigation unit."}[level]
    report = {"score": score, "level": level, "indicators": indicators, "action": action, "analyst_note": analyst_note}
    save_finding(claim.claim_id, AGENT, "fraud_report", {"low": "PASS", "medium": "WARN", "high": "FAIL"}[level],
                 f"Fraud risk {level} ({score}/100). {action}", report)
    if level != "low":
        remember(claim.customer_id, claim.claim_id, AGENT,
                 f"Fraud risk {level}: " + "; ".join(i["indicator"] for i in indicators) + ".")
    return report


register_tools([get_claim_history, detect_duplicate, check_claim_frequency, calculate_fraud_score, generate_fraud_report])


def build():
    return make_agent(
        AGENT, "You are the Fraud Agent of a motor-insurance claims team. You look for duplicate claims, unusual "
               "claim patterns and other fraud indicators on a registered claim.", "fraud-detection")
