"""SQLite via SQLAlchemy: tables, dummy data and the helpers every agent shares.

Agents pass only ids between each other (claim_id); everything else lives here, so no
agent has to re-type a date or an amount.
"""
import csv
import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy import JSON, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from config import DATA, DB_URL
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
    outcome: Mapped[str | None]  # BLOCKED, NOT_REGISTERED, or the claim's status once it was processed
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


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
    """Record a check. Findings are append-only: re-running a check adds a result, it never erases the earlier one."""
    with session() as s:
        s.add(Finding(claim_id=claim_id, agent=agent, name=name, status=status, summary=summary, data=data or {}))
        s.commit()


def get_findings(claim_id: str, history: bool = False) -> list[Finding]:
    """The latest result of each check, in the order the checks first ran. history=True returns every result ever recorded."""
    with session() as s:
        rows = list(s.scalars(select(Finding).where(Finding.claim_id == claim_id).order_by(Finding.id)))
    return rows if history else list({f.name: f for f in rows}.values())


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


def set_outcome(submission_id: str, outcome: str) -> None:
    with session() as s:
        s.get(Submission, submission_id).outcome = outcome
        s.commit()


def _convert(column, text: str):
    if text == "":
        return None
    if isinstance(column.type, JSON):
        return text.split("|")
    kind = column.type.python_type
    if kind is bool:
        return text == "true"
    if kind is date:
        return date.fromisoformat(text)
    return kind(text)


def read_rows(name: str) -> list[dict]:
    """A table from data/<name>.csv as a list of dicts."""
    with open(DATA / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_csv(s: Session, model) -> None:
    for row in read_rows(model.__tablename__):
        s.add(model(**{c.name: _convert(c, row[c.name]) for c in model.__table__.columns if c.name in row}))


def init_db(reset: bool = False) -> None:
    """Create the tables and, if they are empty, load the dummy data from data/*.csv."""
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session() as s:
        if s.scalar(select(func.count()).select_from(Customer)) == 0:
            for model in (Customer, Vehicle, Policy, Claim, Memory):
                _load_csv(s, model)
            s.commit()


def golden_claims() -> list[dict]:
    """The golden dataset, data/golden_claims.json: submissions with the recommendation, payout and fraud level each must produce."""
    return json.loads((DATA / "golden_claims.json").read_text(encoding="utf-8"))
