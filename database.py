"""SQLite via SQLAlchemy: tables, dummy data and the helpers every agent shares.

Agents pass only ids between each other (claim_id); everything else lives here, so no
agent has to re-type a date or an amount.
"""
import random
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from faker import Faker
from sqlalchemy import JSON, create_engine, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from config import DB_URL, ROOT
from schemas import ClaimRequest

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    phone: Mapped[str]
    email: Mapped[str]
    city: Mapped[str]
    kyc_verified: Mapped[bool] = mapped_column(default=True)


class Vehicle(Base):
    __tablename__ = "vehicles"
    vehicle_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str]
    make: Mapped[str]
    model: Mapped[str]
    variant: Mapped[str]
    segment: Mapped[str]  # hatchback | sedan | suv - drives repair costs
    engine_cc: Mapped[int]
    purchase_date: Mapped[date]
    registration_no: Mapped[str]


class Policy(Base):
    __tablename__ = "policies"
    policy_id: Mapped[str] = mapped_column(primary_key=True)
    customer_id: Mapped[str]
    vehicle_id: Mapped[str]
    policy_type: Mapped[str]  # Comprehensive | Third-Party Only
    start_date: Mapped[date]
    end_date: Mapped[date]
    status: Mapped[str]  # ACTIVE | LAPSED | CANCELLED
    idv: Mapped[float]  # insured declared value: the most the insurer pays out
    zero_dep: Mapped[bool] = mapped_column(default=False)
    voluntary_deductible: Mapped[float] = mapped_column(default=0)
    ncb_percent: Mapped[int] = mapped_column(default=0)


class Claim(Base):
    __tablename__ = "claims"
    claim_id: Mapped[str] = mapped_column(primary_key=True)
    submission_id: Mapped[str | None]
    customer_id: Mapped[str]
    policy_id: Mapped[str]
    incident_type: Mapped[str]
    incident_date: Mapped[date]
    reported_date: Mapped[date]
    location: Mapped[str]
    description: Mapped[str]
    estimated_damage: Mapped[float]
    damaged_parts: Mapped[list] = mapped_column(JSON, default=list)
    third_party_involved: Mapped[bool] = mapped_column(default=False)
    alcohol_involved: Mapped[bool] = mapped_column(default=False)
    driver_licence_valid: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="SUBMITTED")
    recommendation: Mapped[str | None]
    rationale: Mapped[str | None]
    payout: Mapped[float] = mapped_column(default=0)
    reviewer_decision: Mapped[str | None]
    reviewer_note: Mapped[str | None]
    notification: Mapped[str | None]


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[str]
    doc_type: Mapped[str]


class Finding(Base):
    """One row per check an agent ran: the execution log shown to the human reviewer."""

    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[str]
    agent: Mapped[str]
    name: Mapped[str]
    status: Mapped[str]  # PASS | WARN | FAIL | INFO
    summary: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class Memory(Base):
    """Notes that outlive a claim and are shown to the agents on the customer's next one."""

    __tablename__ = "memory"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[str]
    claim_id: Mapped[str]
    agent: Mapped[str]
    note: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class Submission(Base):
    """A validated claim waiting for the Claim Agent to register it."""

    __tablename__ = "submissions"
    submission_id: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    claim_id: Mapped[str | None]


@contextmanager
def session():
    with Session(engine, expire_on_commit=False) as s:
        yield s


def claim_bundle(s: Session, claim_id: str):
    """(claim, policy, vehicle) for a claim, or a ValueError the model can read and recover from."""
    claim = s.get(Claim, claim_id.strip().upper())
    if claim is None:
        raise ValueError(f"Unknown claim_id {claim_id!r}")
    policy = s.get(Policy, claim.policy_id)
    vehicle = s.get(Vehicle, policy.vehicle_id) if policy else None
    return claim, policy, vehicle


def next_claim_id(s: Session) -> str:
    return f"CLM-{date.today().year}-{s.scalar(select(func.count()).select_from(Claim)) + 1:04d}"


def save_finding(claim_id: str, agent: str, name: str, status: str, summary: str, data: dict | None = None):
    """Record a check. Re-running a check replaces its earlier result."""
    with session() as s:
        s.execute(delete(Finding).where(Finding.claim_id == claim_id, Finding.name == name))
        s.add(Finding(claim_id=claim_id, agent=agent, name=name, status=status, summary=summary, data=data or {}))
        s.commit()


def get_findings(claim_id: str) -> list[Finding]:
    with session() as s:
        return list(s.scalars(select(Finding).where(Finding.claim_id == claim_id).order_by(Finding.id)))


def remember(customer_id: str, claim_id: str, agent: str, note: str) -> None:
    with session() as s:
        s.add(Memory(customer_id=customer_id, claim_id=claim_id, agent=agent, note=note))
        s.commit()


def recall(customer_id: str, limit: int = 5) -> list[str]:
    with session() as s:
        rows = s.scalars(select(Memory).where(Memory.customer_id == customer_id).order_by(Memory.id.desc()).limit(limit))
        return [f"{m.created_at:%d-%b-%Y}: {m.note}" for m in rows]


def stage_submission(req: ClaimRequest) -> str:
    submission_id = f"SUB-{uuid.uuid4().hex[:8]}"
    with session() as s:
        s.add(Submission(submission_id=submission_id, payload=req.model_dump(mode="json")))
        s.commit()
    return submission_id


def claim_for_submission(submission_id: str) -> str | None:
    with session() as s:
        sub = s.get(Submission, submission_id)
        return sub.claim_id if sub else None


def init_db(reset: bool = False) -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session() as s:
        if s.scalar(select(func.count()).select_from(Customer)) == 0:
            _seed(s)
            s.commit()


# ---------------------------------------------------------------------------
# Dummy data: small, but shaped like real motor claims in India
# ---------------------------------------------------------------------------

DOCS_ALL = ["claim_form", "rc_copy", "driving_licence", "policy_copy", "photos", "repair_estimate"]

# customer_id, name, city | make, model, variant, segment, cc, ex-showroom price, purchased, registration
# | policy type, start, end, status, zero-dep, voluntary deductible, NCB %
_KEY_CUSTOMERS = [
    ("CUST1001", "Arjun Mehta", "Pune", ("Hyundai", "Creta", "SX", "suv", 1497, 1650000, date(2022, 6, 10), "MH12 KL 4521"),
     ("Comprehensive", date(2026, 3, 15), date(2027, 3, 14), "ACTIVE", False, 0, 20)),
    ("CUST1002", "Priya Nair", "Bengaluru", ("Maruti Suzuki", "Swift", "VXi", "hatchback", 1197, 700000, date(2019, 8, 22), "KA05 MN 8812"),
     ("Comprehensive", date(2026, 2, 10), date(2027, 2, 9), "ACTIVE", False, 0, 35)),
    ("CUST1003", "Vikram Singh", "Delhi", ("Tata", "Nexon", "XZ+", "suv", 1199, 1150000, date(2021, 9, 4), "DL3C AB 1290"),
     ("Comprehensive", date(2025, 10, 1), date(2026, 9, 30), "LAPSED", False, 0, 25)),
    ("CUST1004", "Sneha Kulkarni", "Mumbai", ("Honda", "City", "ZX", "sedan", 1498, 1550000, date(2023, 5, 30), "MH02 CD 7734"),
     ("Third-Party Only", date(2026, 4, 1), date(2027, 3, 31), "ACTIVE", False, 0, 0)),
    ("CUST1005", "Rohan Desai", "Ahmedabad", ("Mahindra", "XUV700", "AX7", "suv", 1999, 2400000, date(2023, 2, 14), "GJ01 RT 3345"),
     ("Comprehensive", date(2026, 1, 20), date(2027, 1, 19), "ACTIVE", False, 0, 0)),
    ("CUST1006", "Ananya Iyer", "Chennai", ("Toyota", "Innova Hycross", "ZX", "suv", 1987, 2950000, date(2023, 8, 10), "TN09 BQ 2201"),
     ("Comprehensive", date(2026, 11, 5), date(2027, 11, 4), "ACTIVE", True, 0, 50)),
    ("CUST1007", "Kavita Reddy", "Hyderabad", ("Maruti Suzuki", "Baleno", "Delta", "hatchback", 1197, 850000, date(2022, 3, 18), "TS09 EK 6612"),
     ("Comprehensive", date(2026, 5, 12), date(2027, 5, 11), "ACTIVE", False, 0, 20)),
]

_CATALOG = [
    ("Maruti Suzuki", "Dzire", "VXi", "sedan", 1197, 900000), ("Hyundai", "i20", "Sportz", "hatchback", 1197, 950000),
    ("Tata", "Punch", "Adventure", "suv", 1199, 850000), ("Kia", "Seltos", "HTX", "suv", 1497, 1500000),
    ("Mahindra", "Scorpio-N", "Z8", "suv", 1997, 2000000),
]
_STATE_CODES = ["MH", "KA", "DL", "TN", "GJ", "UP", "RJ", "TS"]
_HISTORY_PARTS = ["front bumper", "rear bumper", "headlight", "tail lamp", "side mirror", "front door"]

# (customer, incident date, type, description, parts, amount, status, payout)
_KEY_HISTORY = [
    ("CUST1001", date(2025, 9, 2), "accident", "Windshield cracked by a stone on the expressway.", ["windshield"], 25000, "APPROVED", 24000),
    ("CUST1002", date(2026, 11, 12), "accident", "Collided with an auto-rickshaw at a junction; front bumper cracked.", ["front bumper"], 15000, "APPROVED", 9000),
    ("CUST1005", date(2026, 1, 30), "accident", "Scraped a pillar in a basement car park; front bumper damaged.", ["front bumper"], 22000, "APPROVED", 16000),
    ("CUST1005", date(2026, 4, 11), "accident", "Door dented by a passing truck.", ["front door"], 37000, "APPROVED", 28000),
    ("CUST1005", date(2026, 7, 5), "accident", "Headlight broken; garage estimate could not be verified.", ["headlight"], 20000, "REJECTED", 0),
    ("CUST1005", date(2026, 9, 19), "accident", "Bonnet dented in a minor collision.", ["bonnet"], 33000, "APPROVED", 24500),
]

_MEMORY_NOTES = {
    "CUST1002": ("fraud_agent", date(2026, 11, 12), "Front-bumper claim settled; any new claim for that incident is a duplicate."),
    "CUST1005": ("fraud_agent", date(2026, 9, 19), "Four claims in under 12 months (one rejected). Check frequency and reporting delay on the next one."),
}


def _idv(price: float, purchased: date, on: date) -> float:
    """Insured declared value: ex-showroom price less the standard age depreciation."""
    age = (on - purchased).days / 365.25
    rate = next((r for limit, r in [(0.5, .05), (1, .15), (2, .20), (3, .30), (4, .40), (5, .50)] if age < limit), 0.55)
    return round(price * (1 - rate), -3)


def _seed(s: Session) -> None:
    fake, rng = Faker("en_IN"), random.Random(2026)
    Faker.seed(2026)
    people = [(c[0], c[1], c[2], c[3], c[4]) for c in _KEY_CUSTOMERS]

    for n in range(8, 13):  # five background customers
        make, model, variant, segment, cc, price = rng.choice(_CATALOG)
        purchased = date(rng.randint(2019, 2025), rng.randint(1, 12), rng.randint(1, 28))
        start = date(2026, rng.randint(1, 9), rng.randint(1, 28))
        reg = f"{rng.choice(_STATE_CODES)}{rng.randint(1, 20):02d} {''.join(rng.choices('ABCDEFGHJKLMNPRST', k=2))} {rng.randint(1000, 9999)}"
        people.append((f"CUST10{n:02d}", fake.name(), fake.city(),
                       (make, model, variant, segment, cc, price, purchased, reg),
                       ("Comprehensive", start, date(start.year + 1, start.month, start.day), "ACTIVE", rng.random() < .3, 0, rng.choice([0, 20, 25, 35]))))

    history = list(_KEY_HISTORY)
    for cid, *_ in people[7:]:
        for _ in range(rng.randint(0, 2)):
            part = rng.choice(_HISTORY_PARTS)
            amount = rng.randrange(8000, 60000, 500)
            status = rng.choice(["APPROVED", "APPROVED", "REJECTED"])
            history.append((cid, date(2025, rng.randint(6, 12), rng.randint(1, 28)), "accident",
                            f"Minor collision; {part} damaged.", [part], amount, status, 0 if status == "REJECTED" else amount * .75))

    for n, (cid, name, city, v, p) in enumerate(people, 1):
        s.add(Customer(customer_id=cid, name=name, city=city, phone=fake.phone_number(),
                       email=f"{name.lower().replace(' ', '.')}@example.in"))
        s.add(Vehicle(vehicle_id=f"VEH{1000 + n}", customer_id=cid, make=v[0], model=v[1], variant=v[2], segment=v[3],
                      engine_cc=v[4], purchase_date=v[6], registration_no=v[7]))
        s.add(Policy(policy_id=f"MOT2026{n:03d}", customer_id=cid, vehicle_id=f"VEH{1000 + n}", policy_type=p[0],
                     start_date=p[1], end_date=p[2], status=p[3], idv=_idv(v[5], v[6], p[1]),
                     zero_dep=p[4], voluntary_deductible=p[5], ncb_percent=p[6]))
    s.flush()

    policy_of = {cid: f"MOT2026{n:03d}" for n, (cid, *_) in enumerate(people, 1)}
    claim_ids = {}
    for seq, (cid, when, kind, text, parts, amount, status, payout) in enumerate(sorted(history, key=lambda h: (h[1], h[0])), 1):
        claim_ids[(cid, when)] = f"CLM-{when.year}-{seq:04d}"
        s.add(Claim(claim_id=claim_ids[(cid, when)], customer_id=cid, policy_id=policy_of[cid], incident_type=kind,
                    incident_date=when, reported_date=when, location="On record", description=text, estimated_damage=amount,
                    damaged_parts=parts, status=status, recommendation=status, payout=payout))
    for cid, (agent, when, note) in _MEMORY_NOTES.items():
        s.add(Memory(customer_id=cid, claim_id=claim_ids[(cid, when)], agent=agent, note=note))


# Ready-made submissions that exercise each outcome (see main.py). Each uses its own customer,
# so they never trip the duplicate check against one another.
DEMO_CLAIMS = [
    ("Clean accident claim", "APPROVE", dict(
        customer_id="CUST1001", policy_id="MOT2026001", incident_type="accident", incident_date=date(2026, 11, 20),
        reported_date=date(2026, 11, 22), location="Baner Road, Pune", estimated_damage=80000,
        description="Hit a stationary divider while avoiding a stray dog; front bumper, bonnet and headlight damaged.",
        damaged_parts=["front bumper", "bonnet", "headlight"], documents=DOCS_ALL)),
    ("Duplicate of an already settled claim", "INVESTIGATE", dict(
        customer_id="CUST1002", policy_id="MOT2026002", incident_type="accident", incident_date=date(2026, 11, 12),
        reported_date=date(2026, 11, 14), location="Outer Ring Road, Bengaluru", estimated_damage=15500,
        description="Collided with an auto-rickshaw at a junction; front bumper cracked.",
        damaged_parts=["front bumper"], documents=DOCS_ALL)),
    ("Lapsed policy", "REJECT", dict(
        customer_id="CUST1003", policy_id="MOT2026003", incident_type="accident", incident_date=date(2026, 11, 18),
        reported_date=date(2026, 11, 19), location="Ring Road, Delhi", estimated_damage=46000,
        description="Side-swiped by a truck; left front door and side mirror damaged.",
        damaged_parts=["front door", "side mirror"], documents=DOCS_ALL)),
    ("Third-party-only policy (own damage not covered)", "REJECT", dict(
        customer_id="CUST1004", policy_id="MOT2026004", incident_type="accident", incident_date=date(2026, 11, 17),
        reported_date=date(2026, 11, 18), location="Western Express Highway, Mumbai", estimated_damage=31000,
        description="Reversed into a pillar in a parking lot; rear bumper and tail lamp damaged.",
        damaged_parts=["rear bumper", "tail lamp"], documents=DOCS_ALL)),
    ("Frequent claimant, reported late", "INVESTIGATE", dict(
        customer_id="CUST1005", policy_id="MOT2026005", incident_type="accident", incident_date=date(2026, 11, 16),
        reported_date=date(2026, 11, 25), location="SG Highway, Ahmedabad", estimated_damage=34000,
        description="Rear bumper and tail lamp damaged in a parking-lot collision.",
        damaged_parts=["rear bumper", "tail lamp"], documents=DOCS_ALL)),
    ("Documents missing, policy only 15 days old", "INVESTIGATE", dict(
        customer_id="CUST1006", policy_id="MOT2026006", incident_type="accident", incident_date=date(2026, 11, 20),
        reported_date=date(2026, 11, 21), location="Anna Salai, Chennai", estimated_damage=58000, third_party_involved=True,
        description="Hit by a two-wheeler while changing lanes; rear door and bumper damaged.",
        damaged_parts=["rear door", "rear bumper"], documents=["claim_form", "rc_copy", "policy_copy", "photos"])),
    ("Drunk-driving exclusion", "REJECT", dict(
        customer_id="CUST1007", policy_id="MOT2026007", incident_type="accident", incident_date=date(2026, 11, 19),
        reported_date=date(2026, 11, 20), location="Jubilee Hills, Hyderabad", estimated_damage=36000, alcohol_involved=True,
        description="Lost control after a night out and hit a tree; bonnet and radiator damaged.",
        damaged_parts=["bonnet", "radiator"], documents=DOCS_ALL)),
]
