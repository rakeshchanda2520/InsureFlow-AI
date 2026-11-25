"""Assessment Agent: damage estimate, depreciation, deductible, payout and the recommendation.

The method, in order. Each step is one plain function below and one tool; the model never does the arithmetic.

    1. estimate      price each damaged part (parts + labour + paint) from data/parts.csv, scaled to the car's segment
    2. depreciation  take depreciation off each part, by material and the car's age (data/depreciation.csv)
    3. deductible    the compulsory excess for the engine size, plus the customer's voluntary one
    4. payout        step 1 - 2 - 3, never more than the insured value, and nothing under a third-party-only policy
    5. decision      APPROVE, REJECT or INVESTIGATE, from the policy and fraud findings (_decide)

The rates are data, not code: edit the CSVs to change a price or a depreciation band.
"""
from andromeda.tools import register_tools, tool

from agents import make_agent
from config import inr
from database import claim_bundle, get_findings, read_rows, save_finding, session

AGENT = "assessment_agent"
PARTS = {row["part"]: row for row in read_rows("parts")}
DEPRECIATION = read_rows("depreciation")
SEGMENT_FACTOR = {"hatchback": 1.0, "sedan": 1.25, "suv": 1.5}  # the price list is for a hatchback
REQUIRED_CHECKS = ("policy_verification", "coverage", "exclusions", "documents", "fraud_report")


def _case(claim_id: str):
    with session() as s:
        claim, policy, vehicle = claim_bundle(s, claim_id)
    if policy is None or vehicle is None:
        raise ValueError("This claim has no valid policy or vehicle, so it cannot be assessed.")
    return claim, policy, vehicle


def _estimate(claim, policy, vehicle) -> dict:
    if claim.incident_type == "theft":  # a stolen car is a total loss: the insurer owes the insured value
        return {"items": [], "unpriced": [], "assessed": policy.idv}

    factor = SEGMENT_FACTOR[vehicle.segment]
    items = []
    for name in claim.damaged_parts:
        if name in PARTS:
            row = PARTS[name]
            items.append({"part": name, "material": row["material"],
                          "parts": round(float(row["parts_cost"]) * factor),
                          "labour": round(float(row["labour"]) * factor),
                          "paint": round(float(row["paint"]) * factor)})
    return {"items": items, "unpriced": [n for n in claim.damaged_parts if n not in PARTS],
            "assessed": sum(i["parts"] + i["labour"] + i["paint"] for i in items)}


def _depreciation_rate(material: str, age: float) -> float:
    """The first row in depreciation.csv for this material whose age band the car falls under."""
    return next(float(r["rate"]) for r in DEPRECIATION if r["material"] == material and age < float(r["up_to_age_years"]))


def _depreciation(claim, policy, vehicle, items: list[dict]) -> dict:
    age = (claim.incident_date - vehicle.purchase_date).days / 365.25
    lines = []
    for item in items:
        waived = policy.zero_dep and item["material"] != "tyre"  # the add-on covers everything except tyres
        rate = 0.0 if waived else _depreciation_rate(item["material"], age)
        lines.append({"part": item["part"], "rate": rate, "amount": round(item["parts"] * rate)})
    return {"vehicle_age_years": round(age, 1), "lines": lines, "total": sum(line["amount"] for line in lines)}


def _deductible(policy, vehicle) -> dict:
    compulsory = 1000 if vehicle.engine_cc <= 1500 else 2000  # private cars, up to / above 1500cc
    return {"compulsory": compulsory, "voluntary": policy.voluntary_deductible,
            "total": compulsory + policy.voluntary_deductible}


def _payout(claim, policy, vehicle) -> dict:
    estimate = _estimate(claim, policy, vehicle)
    depreciation = _depreciation(claim, policy, vehicle, estimate["items"])
    deductible = _deductible(policy, vehicle)
    payable = estimate["assessed"] - depreciation["total"] - deductible["total"]
    covered = policy.policy_type == "Comprehensive"
    return {"assessed": estimate["assessed"], "depreciation": depreciation["total"], "deductible": deductible["total"],
            "payable": payable, "idv": policy.idv, "payout": max(0, min(payable, policy.idv)) if covered else 0}


def _decide(found: dict) -> tuple[str, list[str]]:
    """The recommendation rules, in order: a failed policy check rejects; anything unresolved goes to a person."""
    rejections = [found[n].summary for n in ("policy_verification", "coverage", "exclusions") if found[n].status == "FAIL"]
    if rejections:
        return "REJECT", rejections

    fraud, docs = found["fraud_report"].data, found["documents"].data
    reviews = []
    if fraud["level"] != "low":
        reviews.append(f"Fraud risk {fraud['level']} ({fraud['score']}/100): "
                       + "; ".join(i["indicator"] for i in fraud["indicators"]) + ".")
    if docs["missing"]:
        reviews.append("Further information needed: missing " + ", ".join(docs["missing"]) + ".")
    if reviews:
        return "INVESTIGATE", reviews
    return "APPROVE", ["Policy valid, loss covered, no exclusion, documents complete, low fraud risk."]


@tool
def estimate_damage(claim_id: str) -> dict:
    """Estimate the repair cost (parts, labour, paint) of the damaged parts and compare it with the amount claimed."""
    claim, policy, vehicle = _case(claim_id)
    estimate = _estimate(claim, policy, vehicle)
    variance = claim.estimated_damage / estimate["assessed"] - 1 if estimate["assessed"] else 0
    summary = f"Assessed repair cost {inr(estimate['assessed'])} against {inr(claim.estimated_damage)} claimed ({variance:+.0%})."
    if estimate["unpriced"]:
        summary += f" Could not price: {', '.join(estimate['unpriced'])}."
    save_finding(claim.claim_id, AGENT, "damage_estimate", "WARN" if estimate["unpriced"] or variance > 0.25 else "INFO",
                 summary, estimate)
    return {"assessed": estimate["assessed"], "claimed": claim.estimated_damage, "variance": f"{variance:+.0%}",
            "items": estimate["items"], "unpriced_parts": estimate["unpriced"]}


@tool
def calculate_depreciation(claim_id: str) -> dict:
    """Calculate depreciation on the replaced parts using the vehicle's age and the policy's add-ons."""
    claim, policy, vehicle = _case(claim_id)
    result = _depreciation(claim, policy, vehicle, _estimate(claim, policy, vehicle)["items"])
    save_finding(claim.claim_id, AGENT, "depreciation", "INFO",
                 f"Depreciation {inr(result['total'])} on a {result['vehicle_age_years']}-year-old vehicle"
                 f"{' (zero-depreciation add-on)' if policy.zero_dep else ''}.", result)
    return result


@tool
def calculate_deductible(claim_id: str) -> dict:
    """Calculate the compulsory plus voluntary deductible the customer bears."""
    claim, policy, vehicle = _case(claim_id)
    result = _deductible(policy, vehicle)
    save_finding(claim.claim_id, AGENT, "deductible", "INFO", f"Deductible {inr(result['total'])}.", result)
    return result


@tool
def calculate_payout(claim_id: str) -> dict:
    """Calculate the payout: assessed cost less depreciation and deductible, capped at the insured value."""
    claim, policy, vehicle = _case(claim_id)
    result = _payout(claim, policy, vehicle)
    save_finding(claim.claim_id, AGENT, "payout", "INFO",
                 f"Payable if the claim is accepted: {inr(result['payout'])} ({inr(result['assessed'])} assessed - "
                 f"{inr(result['depreciation'])} depreciation - {inr(result['deductible'])} deductible).", result)
    return result


@tool
def generate_decision(claim_id: str, rationale: str) -> dict:
    """Generate the recommendation (APPROVE, REJECT or INVESTIGATE) from the policy and fraud findings.

    rationale is your own two-sentence explanation. Fails if the policy or fraud checks have not been run.
    """
    claim, policy, vehicle = _case(claim_id)
    found = {f.name: f for f in get_findings(claim.claim_id)}
    missing = [name for name in REQUIRED_CHECKS if name not in found]
    if missing:
        raise ValueError(f"Cannot decide yet - these checks are missing: {', '.join(missing)}. "
                         "They come from the policy_agent and fraud_agent, which must run first.")

    recommendation, reasons = _decide(found)
    breakdown = _payout(claim, policy, vehicle)
    payout = 0 if recommendation == "REJECT" else breakdown["payout"]

    with session() as s:
        row, _, _ = claim_bundle(s, claim.claim_id)
        row.recommendation, row.rationale, row.payout, row.status = recommendation, rationale, payout, "PENDING_REVIEW"
        s.commit()
    save_finding(claim.claim_id, AGENT, "decision", {"APPROVE": "PASS", "INVESTIGATE": "WARN", "REJECT": "FAIL"}[recommendation],
                 f"Recommendation: {recommendation}.",
                 {"recommendation": recommendation, "reasons": reasons, "payout": payout, "breakdown": breakdown})
    return {"recommendation": recommendation, "reasons": reasons, "estimated_payout": inr(payout)}


register_tools([estimate_damage, calculate_depreciation, calculate_deductible, calculate_payout, generate_decision])


def build():
    return make_agent(
        AGENT, "You are the Assessment Agent of a motor-insurance claims team. You estimate the damage, work out "
               "the payout and produce the claim recommendation once the policy and fraud checks exist.", "damage-assessment")
