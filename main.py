"""InsureFlow AI: command-line entry point.

    python main.py              interactive menu
    python main.py --demo 3     run demo claim 3, then ask for the reviewer's decision
    python main.py --reset      rebuild the SQLite database with fresh dummy data
"""
import argparse
import sys
from datetime import date

from config import inr  # first: loads .env before Andromeda is imported

from pydantic import ValidationError
from sqlalchemy import select

from agents.assessment_agent import PARTS
from database import DEMO_CLAIMS, DOCS_ALL, Claim, Customer, claim_bundle, get_findings, init_db, session
from schemas import ClaimRequest
from supervisor import finalize_claim, process_claim

sys.stdout.reconfigure(encoding="utf-8")  # so the rupee sign prints on Windows consoles

MENU = """
==============================================
  InsureFlow AI - motor claims processing
==============================================
  1) Run a demo claim
  2) Submit a new claim
  3) Show a claim report
  4) List claims
  5) Reset demo data
  q) Quit
=============================================="""

BADGE = {"PASS": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[INFO]"}
AGENTS = [("claim_agent", "Claim Agent"), ("policy_agent", "Policy Agent"), ("fraud_agent", "Fraud Agent"),
          ("assessment_agent", "Assessment Agent"), ("human_review", "Human review")]
REVIEW = {"a": "APPROVED", "r": "REJECTED", "i": "INVESTIGATION"}


def show_report(claim_id: str) -> None:
    with session() as s:
        claim, policy, vehicle = claim_bundle(s, claim_id)
        customer = s.get(Customer, claim.customer_id)
    findings = get_findings(claim_id)

    print(f"\n{'=' * 78}\nCLAIM {claim.claim_id}   status: {claim.status}")
    print(f"{customer.name} ({customer.customer_id}), {vehicle.make} {vehicle.model} {vehicle.registration_no}, "
          f"policy {policy.policy_id} ({policy.policy_type})")
    print(f"{claim.incident_type} on {claim.incident_date}, claimed {inr(claim.estimated_damage)}\n{'=' * 78}")

    for key, label in AGENTS:
        rows = [f for f in findings if f.agent == key]
        if rows:
            print(f"\n{label}")
            for f in rows:
                print(f"  {BADGE[f.status]} {f.summary}")

    decision = next((f for f in findings if f.name == "decision"), None)
    if decision:
        b = decision.data["breakdown"]
        print(f"\n{'-' * 78}\nRECOMMENDATION: {decision.data['recommendation']}")
        for reason in decision.data["reasons"]:
            print(f"  - {reason}")
        print(f"\n  Assessed repair cost   {inr(b['assessed']):>12}\n  Less depreciation      {inr(b['depreciation']):>12}"
              f"\n  Less deductible        {inr(b['deductible']):>12}\n  Insured value (cap)    {inr(b['idv']):>12}"
              f"\n  Estimated payout       {inr(decision.data['payout']):>12}")
        if claim.rationale:
            print(f"\n  Agent's rationale: {claim.rationale}")
    if claim.notification:
        print(f"\nCustomer notification:\n  {claim.notification}")


def ask(prompt: str, default: str = "") -> str:
    answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return answer or default


def review(claim_id: str) -> None:
    while (choice := ask("\nReviewer decision - [a]pprove, [r]eject, [i]nvestigate further, [s]kip").lower()) not in {*REVIEW, "s"}:
        print("Please enter a, r, i or s.")
    if choice == "s":
        print("Left pending review.")
        return
    notification = finalize_claim(claim_id, REVIEW[choice], ask("Reviewer note (optional)"))
    print(f"\nClaim {REVIEW[choice]}. Notification sent to the customer:\n  {notification}")


def run(req: ClaimRequest) -> None:
    print("\nAgents are working on the claim (this takes a minute)...")
    try:
        result = process_claim(req)
    except Exception as exc:  # most often a missing OPENAI_API_KEY or an unreachable model
        print(f"[error] {type(exc).__name__}: {exc}")
        return
    if result.claim_id is None:
        print(f"\nClaim not processed ({result.status}): {result.summary}")
        return
    show_report(result.claim_id)
    if result.status != "PENDING_REVIEW":
        print(f"\nThe agents did not finish (status {result.status}). Supervisor said: {result.summary}")
        return
    review(result.claim_id)


def choose_demo(number: int | None = None) -> None:
    if number is None:
        print()
        for n, (title, expected, _) in enumerate(DEMO_CLAIMS, 1):
            print(f"  {n}) {title}  (expect {expected})")
        number = int(ask("Demo number") or 0)
    if not 1 <= number <= len(DEMO_CLAIMS):
        print("No such demo.")
        return
    title, _, fields = DEMO_CLAIMS[number - 1]
    print(f"\nDemo: {title}\n  {fields['description']}")
    run(ClaimRequest(**fields))


def new_claim() -> None:
    print(f"\nKnown parts: {', '.join(PARTS)}")
    parts = ask("Damaged parts (comma separated)")
    docs = ask(f"Documents received (comma separated, or 'all' for {len(DOCS_ALL)} standard ones)", "all")
    try:
        req = ClaimRequest(
            customer_id=ask("Customer ID", "CUST1001"), policy_id=ask("Policy ID", "MOT2026001"),
            incident_type=ask("Incident type (accident/theft/fire/flood/vandalism)", "accident"),
            incident_date=ask("Incident date (YYYY-MM-DD)"), reported_date=ask("Reported date", str(date.today())),
            location=ask("Location"), description=ask("What happened"),
            estimated_damage=ask("Estimated damage in rupees"), damaged_parts=parts.split(","),
            third_party_involved=ask("Third party involved? (y/n)", "n").lower() == "y",
            alcohol_involved=ask("Alcohol involved? (y/n)", "n").lower() == "y",
            driver_licence_valid=ask("Driver had a valid licence? (y/n)", "y").lower() == "y",
            documents=DOCS_ALL if docs.lower() == "all" else docs.split(","),
        )
    except ValidationError as exc:
        print("\nThat claim is not valid:")
        for err in exc.errors():
            print(f"  - {err['loc'][0] if err['loc'] else 'claim'}: {err['msg']}")
        return
    run(req)


def list_claims() -> None:
    with session() as s:
        rows = list(s.scalars(select(Claim).order_by(Claim.claim_id.desc()).limit(15)))
    print(f"\n{'CLAIM':14}{'CUSTOMER':10}{'TYPE':10}{'INCIDENT':12}{'CLAIMED':>12}  {'STATUS':16}RECOMMENDED")
    for c in rows:
        print(f"{c.claim_id:14}{c.customer_id:10}{c.incident_type:10}{str(c.incident_date):12}"
              f"{inr(c.estimated_damage):>12}  {c.status:16}{c.recommendation or '-'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="InsureFlow AI")
    parser.add_argument("--demo", type=int, help="run demo claim N")
    parser.add_argument("--reset", action="store_true", help="rebuild the database with fresh dummy data")
    args = parser.parse_args()

    init_db(reset=args.reset)
    if args.reset:
        print("Database reset.")
    if args.demo:
        return choose_demo(args.demo)
    if args.reset:
        return

    print(MENU)
    while (choice := ask("Choose").lower()) not in {"q", "quit", "exit"}:
        try:
            if choice == "1":
                choose_demo()
            elif choice == "2":
                new_claim()
            elif choice == "3":
                show_report(ask("Claim ID").upper())
            elif choice == "4":
                list_claims()
            elif choice == "5":
                init_db(reset=True)
                print("Demo data reset.")
            else:
                print("Please choose 1-5 or q.")
        except (ValueError, KeyboardInterrupt) as exc:
            print(f"[error] {exc}")
        print(MENU)


if __name__ == "__main__":
    main()
