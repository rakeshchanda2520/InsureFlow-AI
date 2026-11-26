"""Claim Agent: finds the customer and policy and registers the claim."""
from datetime import date

from andromeda.tools import register_tools, tool
from sqlalchemy import or_, select

from agents import make_agent
from config import inr
from database import (Claim, Customer, Document, Policy, Submission, Vehicle, claim_bundle, get_findings,
                      next_claim_id, recall, save_finding, session)

AGENT = "claim_agent"


def _submitted(s, submission_id: str) -> dict:
    """The claim as submitted. Lookups are limited to what it names, so one customer's claim can never read another's."""
    sub = s.get(Submission, submission_id.strip())
    if sub is None:
        raise ValueError(f"Unknown submission_id {submission_id!r}")
    return sub.payload


@tool
def search_customer(query: str) -> list[dict]:
    """Suggest customers that match a name, phone number or vehicle registration. Names are shortened on purpose."""
    if len(query.strip()) < 4:
        raise ValueError("Give at least 4 characters to search.")
    like = f"%{query.strip()}%"
    with session() as s:
        rows = s.execute(
            select(Customer).join(Vehicle, Vehicle.customer_id == Customer.customer_id)
            .where(or_(Customer.name.ilike(like), Customer.phone.ilike(like), Vehicle.registration_no.ilike(like))).limit(3)
        ).scalars()
        return [{"customer_id": c.customer_id, "name": f"{c.name.split()[0]} {c.name.split()[-1][0]}.", "city": c.city} for c in rows]


@tool
def get_customer(customer_id: str, submission_id: str) -> dict:
    """Get the profile, vehicles, policies and remembered notes of the customer who made this submission."""
    with session() as s:
        submitted = _submitted(s, submission_id)
        if customer_id.strip().upper() != submitted["customer_id"]:
            raise ValueError("You may only look up the customer who made this submission.")
        customer = s.get(Customer, submitted["customer_id"])
        if customer is None:
            raise ValueError(f"Customer {customer_id!r} not found.")
        vehicles = list(s.scalars(select(Vehicle).where(Vehicle.customer_id == customer.customer_id)))
        policies = list(s.scalars(select(Policy).where(Policy.customer_id == customer.customer_id)))
    return {
        "customer_id": customer.customer_id, "name": customer.name, "city": customer.city,
        "kyc_verified": customer.kyc_verified,
        "vehicles": [f"{v.make} {v.model} {v.variant} ({v.registration_no})" for v in vehicles],
        "policies": [f"{p.policy_id}: {p.policy_type}, {p.status}" for p in policies],
        "memory": recall(customer.customer_id),
    }


@tool
def get_policy(policy_id: str, submission_id: str) -> dict:
    """Get the policy named in this submission, with its insured vehicle, period, status and add-ons."""
    with session() as s:
        submitted = _submitted(s, submission_id)
        if policy_id.strip().upper() != submitted["policy_id"]:
            raise ValueError("You may only look up the policy named in this submission.")
        policy = s.get(Policy, submitted["policy_id"])
        if policy is None:
            raise ValueError(f"Policy {policy_id!r} not found.")
        vehicle = s.get(Vehicle, policy.vehicle_id)
    return {
        "policy_id": policy.policy_id, "customer_id": policy.customer_id, "type": policy.policy_type,
        "status": policy.status, "period": f"{policy.start_date} to {policy.end_date}", "idv": inr(policy.idv),
        "zero_depreciation": policy.zero_dep, "ncb_percent": policy.ncb_percent,
        "vehicle": f"{vehicle.make} {vehicle.model} {vehicle.variant}, {vehicle.registration_no}",
    }


@tool
def create_claim(submission_id: str) -> dict:
    """Register the submitted claim in the claims system and return its new claim_id. Safe to call twice."""
    with session() as s:
        p = _submitted(s, submission_id)
        sub = s.get(Submission, submission_id.strip())
        if sub.claim_id:
            return {"claim_id": sub.claim_id, "note": "already registered"}

        if s.get(Customer, p["customer_id"]) is None:
            raise ValueError(f"Customer {p['customer_id']} does not exist; the claim cannot be registered.")
        if s.get(Policy, p["policy_id"]) is None:
            raise ValueError(f"Policy {p['policy_id']} does not exist; the claim cannot be registered.")

        claim = Claim(
            claim_id=next_claim_id(s), submission_id=sub.submission_id,
            **{k: p[k] for k in ("customer_id", "policy_id", "incident_type", "location", "description", "estimated_damage",
                                 "damaged_parts", "third_party_involved", "alcohol_involved", "driver_licence_valid")},
            incident_date=date.fromisoformat(p["incident_date"]), reported_date=date.fromisoformat(p["reported_date"]),
        )
        s.add(claim)
        s.add_all(Document(claim_id=claim.claim_id, doc_type=d) for d in p["documents"])
        sub.claim_id = claim.claim_id
        s.commit()

    save_finding(claim.claim_id, AGENT, "registration", "INFO",
                 f"Claim {claim.claim_id} registered: {claim.incident_type} on {claim.incident_date}, "
                 f"claimed {inr(claim.estimated_damage)}.")
    return {"claim_id": claim.claim_id, "status": "SUBMITTED", "incident_date": str(claim.incident_date),
            "claimed_amount": inr(claim.estimated_damage)}


@tool
def update_claim(claim_id: str, field: str, value: str) -> dict:
    """Correct a typo in a claim's description or location. field must be 'description' or 'location'."""
    if field not in ("description", "location"):
        raise ValueError("Only description and location can be corrected. The claimed amount and the decision are off limits.")
    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
        before = getattr(claim, field)
        setattr(claim, field, value)
        s.commit()
    save_finding(claim.claim_id, AGENT, f"correction_{field}", "WARN", f"{field} changed by the agent: {before!r} -> {value!r}")
    return {"claim_id": claim.claim_id, "updated": field}


@tool
def get_claim_status(claim_id: str) -> dict:
    """Get a claim's current status, recommendation, payout and the checks recorded so far."""
    with session() as s:
        claim, _, _ = claim_bundle(s, claim_id)
    return {"claim_id": claim.claim_id, "status": claim.status, "recommendation": claim.recommendation,
            "payout": inr(claim.payout), "checks": [f"{f.agent}/{f.name}: {f.status}" for f in get_findings(claim.claim_id)]}


register_tools([search_customer, get_customer, get_policy, create_claim, update_claim, get_claim_status])


def build():
    return make_agent(
        AGENT, "You are the Claim Agent of a motor-insurance claims team. You identify the customer and "
               "policy behind a submitted claim and register it.", "claim-intake")
