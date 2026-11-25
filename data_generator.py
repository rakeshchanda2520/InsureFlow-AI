"""Writes the dummy data in data/: five CSV tables and the demo submissions.

    python data_generator.py

The CSVs are committed to git and are the source of truth. database.py loads them into SQLite on first
run, so every laptop gets identical data and Faker is only needed to regenerate or extend them.
"""
import csv
import json
import random
from datetime import date
from pathlib import Path

from faker import Faker

from schemas import DOCS_ALL

DATA = Path(__file__).parent / "data"

# customer_id, name, city | make, model, variant, segment, cc, ex-showroom price, purchased, registration
# | policy type, start, end, status, zero-dep, voluntary deductible, NCB %
KEY_CUSTOMERS = [
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

CATALOG = [
    ("Maruti Suzuki", "Dzire", "VXi", "sedan", 1197, 900000), ("Hyundai", "i20", "Sportz", "hatchback", 1197, 950000),
    ("Tata", "Punch", "Adventure", "suv", 1199, 850000), ("Kia", "Seltos", "HTX", "suv", 1497, 1500000),
    ("Mahindra", "Scorpio-N", "Z8", "suv", 1997, 2000000),
]
STATE_CODES = ["MH", "KA", "DL", "TN", "GJ", "UP", "RJ", "TS"]
HISTORY_PARTS = ["front bumper", "rear bumper", "headlight", "tail lamp", "side mirror", "front door"]

# (customer, incident date, description, parts, amount, status, payout)
KEY_HISTORY = [
    ("CUST1001", date(2025, 9, 2), "Windshield cracked by a stone on the expressway.", ["windshield"], 25000, "APPROVED", 24000),
    ("CUST1002", date(2026, 11, 12), "Collided with an auto-rickshaw at a junction; front bumper cracked.", ["front bumper"], 15000, "APPROVED", 9000),
    ("CUST1005", date(2026, 1, 30), "Scraped a pillar in a basement car park; front bumper damaged.", ["front bumper"], 22000, "APPROVED", 16000),
    ("CUST1005", date(2026, 4, 11), "Door dented by a passing truck.", ["front door"], 37000, "APPROVED", 28000),
    ("CUST1005", date(2026, 7, 5), "Headlight broken; garage estimate could not be verified.", ["headlight"], 20000, "REJECTED", 0),
    ("CUST1005", date(2026, 9, 19), "Bonnet dented in a minor collision.", ["bonnet"], 33000, "APPROVED", 24500),
]

MEMORY = {
    "CUST1002": (date(2026, 11, 12), "Front-bumper claim settled; any new claim for that incident is a duplicate."),
    "CUST1005": (date(2026, 9, 19), "Four claims in under 12 months (one rejected). Check frequency and reporting delay on the next one."),
}

# (title, expected recommendation, submission). Each uses its own customer, so they never trip the
# duplicate check against one another.
DEMOS = [
    ("Clean accident claim", "APPROVE", dict(
        customer_id="CUST1001", policy_id="MOT2026001", incident_type="accident", incident_date="2026-11-20",
        reported_date="2026-11-22", location="Baner Road, Pune", estimated_damage=80000,
        description="Hit a stationary divider while avoiding a stray dog; front bumper, bonnet and headlight damaged.",
        damaged_parts=["front bumper", "bonnet", "headlight"], documents=DOCS_ALL)),
    ("Duplicate of an already settled claim", "INVESTIGATE", dict(
        customer_id="CUST1002", policy_id="MOT2026002", incident_type="accident", incident_date="2026-11-12",
        reported_date="2026-11-14", location="Outer Ring Road, Bengaluru", estimated_damage=15500,
        description="Collided with an auto-rickshaw at a junction; front bumper cracked.",
        damaged_parts=["front bumper"], documents=DOCS_ALL)),
    ("Lapsed policy", "REJECT", dict(
        customer_id="CUST1003", policy_id="MOT2026003", incident_type="accident", incident_date="2026-11-18",
        reported_date="2026-11-19", location="Ring Road, Delhi", estimated_damage=46000,
        description="Side-swiped by a truck; left front door and side mirror damaged.",
        damaged_parts=["front door", "side mirror"], documents=DOCS_ALL)),
    ("Third-party-only policy (own damage not covered)", "REJECT", dict(
        customer_id="CUST1004", policy_id="MOT2026004", incident_type="accident", incident_date="2026-11-17",
        reported_date="2026-11-18", location="Western Express Highway, Mumbai", estimated_damage=31000,
        description="Reversed into a pillar in a parking lot; rear bumper and tail lamp damaged.",
        damaged_parts=["rear bumper", "tail lamp"], documents=DOCS_ALL)),
    ("Frequent claimant, reported late", "INVESTIGATE", dict(
        customer_id="CUST1005", policy_id="MOT2026005", incident_type="accident", incident_date="2026-11-16",
        reported_date="2026-11-25", location="SG Highway, Ahmedabad", estimated_damage=34000,
        description="Rear bumper and tail lamp damaged in a parking-lot collision.",
        damaged_parts=["rear bumper", "tail lamp"], documents=DOCS_ALL)),
    ("Documents missing, policy only 15 days old", "INVESTIGATE", dict(
        customer_id="CUST1006", policy_id="MOT2026006", incident_type="accident", incident_date="2026-11-20",
        reported_date="2026-11-21", location="Anna Salai, Chennai", estimated_damage=58000, third_party_involved=True,
        description="Hit by a two-wheeler while changing lanes; rear door and bumper damaged.",
        damaged_parts=["rear door", "rear bumper"], documents=["claim_form", "rc_copy", "policy_copy", "photos"])),
    ("Drunk-driving exclusion", "REJECT", dict(
        customer_id="CUST1007", policy_id="MOT2026007", incident_type="accident", incident_date="2026-11-19",
        reported_date="2026-11-20", location="Jubilee Hills, Hyderabad", estimated_damage=36000, alcohol_involved=True,
        description="Lost control after a night out and hit a tree; bonnet and radiator damaged.",
        damaged_parts=["bonnet", "radiator"], documents=DOCS_ALL)),
]


def idv(price: float, purchased: date, on: date) -> float:
    """Insured declared value: ex-showroom price less the standard age depreciation."""
    age = (on - purchased).days / 365.25
    rate = next((r for limit, r in [(0.5, .05), (1, .15), (2, .20), (3, .30), (4, .40), (5, .50)] if age < limit), 0.55)
    return round(price * (1 - rate), -3)


def write_csv(name: str, rows: list[dict]) -> None:
    with open(DATA / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: str(v).lower() if isinstance(v, bool) else "|".join(v) if isinstance(v, list) else v
                          for k, v in row.items()} for row in rows)


def main() -> None:
    fake, rng = Faker("en_IN"), random.Random(2026)
    Faker.seed(2026)
    people = list(KEY_CUSTOMERS)

    for n in range(8, 13):  # five background customers
        make, model, variant, segment, cc, price = rng.choice(CATALOG)
        purchased = date(rng.randint(2019, 2025), rng.randint(1, 12), rng.randint(1, 28))
        start = date(2026, rng.randint(1, 9), rng.randint(1, 28))
        reg = f"{rng.choice(STATE_CODES)}{rng.randint(1, 20):02d} {''.join(rng.choices('ABCDEFGHJKLMNPRST', k=2))} {rng.randint(1000, 9999)}"
        people.append((f"CUST10{n:02d}", fake.name(), fake.city(), (make, model, variant, segment, cc, price, purchased, reg),
                       ("Comprehensive", start, date(start.year + 1, start.month, start.day), "ACTIVE", rng.random() < .3, 0,
                        rng.choice([0, 20, 25, 35]))))

    history = list(KEY_HISTORY)
    for cid, *_ in people[len(KEY_CUSTOMERS):]:
        for _ in range(rng.randint(0, 2)):
            part, status = rng.choice(HISTORY_PARTS), rng.choice(["APPROVED", "APPROVED", "REJECTED"])
            amount = rng.randrange(8000, 60000, 500)
            history.append((cid, date(2025, rng.randint(6, 12), rng.randint(1, 28)), f"Minor collision; {part} damaged.",
                            [part], amount, status, 0 if status == "REJECTED" else amount * 3 // 4))

    customers, vehicles, policies = [], [], []
    for n, (cid, name, city, v, p) in enumerate(people, 1):
        customers.append(dict(customer_id=cid, name=name, phone=fake.phone_number(), email=f"{name.lower().replace(' ', '.')}@example.in",
                              city=city, kyc_verified=True))
        vehicles.append(dict(vehicle_id=f"VEH{1000 + n}", customer_id=cid, make=v[0], model=v[1], variant=v[2], segment=v[3],
                             engine_cc=v[4], purchase_date=v[6], registration_no=v[7]))
        policies.append(dict(policy_id=f"MOT2026{n:03d}", customer_id=cid, vehicle_id=f"VEH{1000 + n}", policy_type=p[0],
                             start_date=p[1], end_date=p[2], status=p[3], idv=idv(v[5], v[6], p[1]), zero_dep=p[4],
                             voluntary_deductible=p[5], ncb_percent=p[6]))

    policy_of = {p["customer_id"]: p["policy_id"] for p in policies}
    claims, claim_ids = [], {}
    for seq, (cid, when, text, parts, amount, status, payout) in enumerate(sorted(history, key=lambda h: (h[1], h[0])), 1):
        claim_ids[(cid, when)] = f"CLM-{when.year}-{seq:04d}"
        claims.append(dict(claim_id=claim_ids[(cid, when)], customer_id=cid, policy_id=policy_of[cid], incident_type="accident",
                           incident_date=when, reported_date=when, location="On record", description=text,
                           estimated_damage=amount, damaged_parts=parts, status=status, recommendation=status, payout=payout))
    memory = [dict(customer_id=cid, claim_id=claim_ids[(cid, when)], agent="fraud_agent", note=note)
              for cid, (when, note) in MEMORY.items()]

    DATA.mkdir(exist_ok=True)
    for name, rows in [("customers", customers), ("vehicles", vehicles), ("policies", policies), ("claims", claims), ("memory", memory)]:
        write_csv(name, rows)
    (DATA / "demo_claims.json").write_text(
        json.dumps([{"title": t, "expected": e, "claim": c} for t, e, c in DEMOS], indent=2), encoding="utf-8")
    print(f"Wrote {len(customers)} customers, {len(claims)} claims and {len(DEMOS)} demo claims to {DATA}")


if __name__ == "__main__":
    main()
