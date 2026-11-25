"""Assessment Agent: damage estimate, depreciation, deductible, payout and the recommendation.

All arithmetic is plain Python. The model decides which tool to call and explains the result;
it never calculates money itself.
"""
from andromeda.tools import register_tools, tool

from agents import make_agent
from config import inr
from database import claim_bundle, get_findings, save_finding, session

AGENT = "assessment_agent"

# part: (material, part cost, labour, paint) in rupees, priced for a hatchback
PARTS = {
    "front bumper": ("plastic", 9000, 2500, 3000), "rear bumper": ("plastic", 8500, 2500, 3000),
    "bonnet": ("metal", 14000, 3500, 4500), "front door": ("metal", 16000, 4000, 4500),
    "rear door": ("metal", 15000, 4000, 4500), "fender": ("metal", 9500, 3000, 3500),
    "headlight": ("plastic", 12000, 1200, 0), "tail lamp": ("plastic", 6500, 900, 0),
    "side mirror": ("plastic", 5500, 600, 0), "windshield": ("glass", 15000, 1800, 0),
    "radiator": ("metal", 11000, 2000, 0), "tyre": ("tyre", 7500, 500, 0),
}
SEGMENT_FACTOR = {"hatchback": 1.0, "sedan": 1.25, "suv": 1.5}

# Standard depreciation on metal parts by vehicle age (years). Older than 10 years: 50%.
_METAL_BY_AGE = [(0.5, 0.0), (1, .05), (2, .10), (3, .15), (4, .25), (5, .35), (10, .40)]
_FLAT_RATE = {"plastic": .50, "tyre": .50, "glass": 0.0}  # rubber, plastic and tyres 50%; glass none


def _context(claim_id: str):
    with session() as s:
        claim, policy, vehicle = claim_bundle(s, claim_id)
    if policy is None or vehicle is None:
        raise ValueError("This claim has no valid policy or vehicle, so it cannot be assessed.")
    return claim, policy, vehicle


def _estimate(claim, policy, vehicle) -> dict:
    if claim.incident_type == "theft":  # total loss: the insurer owes the insured value
        return {"items": [], "unpriced": [], "assessed": policy.idv, "total_loss": True}
    factor = SEGMENT_FACTOR[vehicle.segment]
    items = [{"part": p, "material": PARTS[p][0], "parts": round(PARTS[p][1] * factor),
              "labour": round(PARTS[p][2] * factor), "paint": round(PARTS[p][3] * factor)}
             for p in claim.damaged_parts if p in PARTS]
    return {"items": items, "unpriced": [p for p in claim.damaged_parts if p not in PARTS],
            "assessed": sum(i["parts"] + i["labour"] + i["paint"] for i in items), "total_loss": False}


def _depreciation(claim, policy, vehicle, items: list[dict]) -> dict:
    age = (claim.incident_date - vehicle.purchase_date).days / 365.25
    metal_rate = next((r for limit, r in _METAL_BY_AGE if age < limit), 0.50)
    lines = []
    for item in items:
        rate = metal_rate if item["material"] == "metal" else _FLAT_RATE[item["material"]]
        if policy.zero_dep and item["material"] != "tyre":
            rate = 0.0  # the zero-depreciation add-on waives everything except tyres
        lines.append({"part": item["part"], "rate": rate, "amount": round(item["parts"] * rate)})
    return {"vehicle_age_years": round(age, 1), "lines": lines, "total": sum(l["amount"] for l in lines)}


def _deductible(policy, vehicle) -> dict:
    compulsory = 1000 if vehicle.engine_cc <= 1500 else 2000  # private cars: up to / above 1500cc
    return {"compulsory": compulsory, "voluntary": policy.voluntary_deductible,
            "total": compulsory + policy.voluntary_deductible}


def _payout(claim, policy, vehicle) -> dict:
    est = _estimate(claim, policy, vehicle)
    dep = _depreciation(claim, policy, vehicle, est["items"])
    ded = _deductible(policy, vehicle)
    payable = est["assessed"] - dep["total"] - ded["total"]
    payout = max(0, min(payable, policy.idv)) if policy.policy_type == "Comprehensive" else 0
    return {"assessed": est["assessed"], "depreciation": dep["total"], "deductible": ded["total"],
            "payable": payable, "idv": policy.idv, "payout": payout}


@tool
def estimate_damage(claim_id: str) -> dict:
    """Estimate the repair cost (parts, labour, paint) of the damaged parts and compare it with the amount claimed."""
    claim, policy, vehicle = _context(claim_id)
    est = _estimate(claim, policy, vehicle)
    variance = claim.estimated_damage / est["assessed"] - 1 if est["assessed"] else 0
    warn = bool(est["unpriced"]) or variance > 0.25
    summary = f"Assessed repair cost {inr(est['assessed'])} against {inr(claim.estimated_damage)} claimed ({variance:+.0%})."
    if est["unpriced"]:
        summary += f" Could not price: {', '.join(est['unpriced'])}."
    save_finding(claim.claim_id, AGENT, "damage_estimate", "WARN" if warn else "INFO", summary, est)
    return {"assessed": est["assessed"], "claimed": claim.estimated_damage, "variance": f"{variance:+.0%}",
            "items": est["items"], "unpriced_parts": est["unpriced"]}


@tool
def calculate_depreciation(claim_id: str) -> dict:
    """Calculate depreciation on the replaced parts using the vehicle's age and the policy's add-ons."""
    claim, policy, vehicle = _context(claim_id)
    dep = _depreciation(claim, policy, vehicle, _estimate(claim, policy, vehicle)["items"])
    save_finding(claim.claim_id, AGENT, "depreciation", "INFO",
                 f"Depreciation {inr(dep['total'])} on a {dep['vehicle_age_years']}-year-old vehicle"
                 f"{' (zero-depreciation add-on)' if policy.zero_dep else ''}.", dep)
    return dep


@tool
def calculate_deductible(claim_id: str) -> dict:
    """Calculate the compulsory plus voluntary deductible the customer bears."""
    claim, policy, vehicle = _context(claim_id)
    ded = _deductible(policy, vehicle)
    save_finding(claim.claim_id, AGENT, "deductible", "INFO", f"Deductible {inr(ded['total'])}.", ded)
    return ded


@tool
def calculate_payout(claim_id: str) -> dict:
    """Calculate the payout: assessed cost less depreciation and deductible, capped at the insured value."""
    claim, policy, vehicle = _context(claim_id)
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
    claim, policy, vehicle = _context(claim_id)
    found = {f.name: f for f in get_findings(claim.claim_id)}
    missing = [n for n in ("policy_verification", "coverage", "exclusions", "documents", "fraud_report") if n not in found]
    if missing:
        raise ValueError(f"Cannot decide yet - these checks are missing: {', '.join(missing)}. "
                         "They come from the policy_agent and fraud_agent, which must run first.")

    rejections = [found[n].summary for n in ("policy_verification", "coverage", "exclusions") if found[n].status == "FAIL"]
    fraud, docs = found["fraud_report"].data, found["documents"].data
    reviews = []
    if fraud["level"] != "low":
        reviews.append(f"Fraud risk {fraud['level']} ({fraud['score']}/100): " + "; ".join(i["indicator"] for i in fraud["indicators"]) + ".")
    if docs["missing"]:
        reviews.append("Further information needed: missing " + ", ".join(docs["missing"]) + ".")

    recommendation = "REJECT" if rejections else "INVESTIGATE" if reviews else "APPROVE"
    reasons = rejections or reviews or ["Policy valid, loss covered, no exclusion, documents complete, low fraud risk."]
    breakdown = _payout(claim, policy, vehicle)
    payout = 0 if recommendation == "REJECT" else breakdown["payout"]

    with session() as s:
        row, _, _ = claim_bundle(s, claim.claim_id)
        row.recommendation, row.rationale, row.payout, row.status = recommendation, rationale, payout, "PENDING_REVIEW"
        s.commit()
    save_finding(claim.claim_id, AGENT, "decision", {"APPROVE": "PASS", "INVESTIGATE": "WARN", "REJECT": "FAIL"}[recommendation],
                 f"Recommendation: {recommendation}.", {"recommendation": recommendation, "reasons": reasons,
                                                       "payout": payout, "breakdown": breakdown})
    return {"recommendation": recommendation, "reasons": reasons, "estimated_payout": inr(payout)}


register_tools([estimate_damage, calculate_depreciation, calculate_deductible, calculate_payout, generate_decision])


def build():
    return make_agent(
        AGENT, "You are the Assessment Agent of a motor-insurance claims team. You estimate the damage, work out "
               "the payout and produce the claim recommendation once the policy and fraud checks exist.", "damage-assessment")
