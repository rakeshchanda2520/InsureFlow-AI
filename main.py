"""InsureFlow AI: command-line entry point.

    python main.py              interactive menu
    python main.py --demo 3     run golden claim 3, then ask for the reviewer's decision
    python main.py --eval       run the golden dataset and compare recommendation, payout and fraud level
    python main.py --redteam    attack the system with adversarial claims
    python main.py --gate       run both and exit 1 unless the quality thresholds are met (for CI)
    python main.py --audit ID   the full recorded history of a claim or submission
    python main.py --reset      rebuild the SQLite database from data/*.csv
"""
import argparse
import sys
from datetime import date

from config import inr  # first: loads .env before Andromeda is imported

from pydantic import ValidationError
from sqlalchemy import select

from agents.assessment_agent import PARTS
from database import Claim, Customer, Submission, claim_bundle, get_findings, golden_claims, init_db, session
from evals import add_golden, gate, run_golden, run_redteam
from observability import OUTCOME, flush
from schemas import DOCS_ALL, ClaimRequest
from supervisor import finalize_claim, process_claim

sys.stdout.reconfigure(encoding="utf-8")  # so the rupee sign prints on Windows consoles

MENU = """
==============================================
  InsureFlow AI - motor claims processing
==============================================
  1) Run a golden claim
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
    with session() as s:
        recommended = claim_bundle(s, claim_id)[0].recommendation
    if OUTCOME.get(recommended) != REVIEW[choice] and ask("The agents recommended differently. Save this as a golden test case? (y/n)", "n").lower() == "y":
        add_golden(claim_id, REVIEW[choice])
        print("Saved to data/golden_claims.json: it now runs with --eval.")


def run(req: ClaimRequest, source: str = "manual") -> None:
    print("\nAgents are working on the claim (this takes a minute)...")
    try:
        result = process_claim(req, source)
    except Exception as exc:  # most often a missing OPENAI_API_KEY or an unreachable model
        print(f"[error] {type(exc).__name__}: {exc}")
        return
    if result.claim_id is None:
        print(f"\nClaim not processed ({result.status}): {result.summary}")
    else:
        show_report(result.claim_id)
        if result.status == "PENDING_REVIEW":
            review(result.claim_id)
        else:
            print(f"\nThe agents did not finish (status {result.status}). Supervisor said: {result.summary}")
    flush()


def choose_demo(number: int | None = None) -> None:
    cases = golden_claims()
    if number is None:
        print()
        for n, case in enumerate(cases, 1):
            print(f"  {n:>2}) {case['title']}  (expect {case['expected']})")
        number = int(ask("Case number") or 0)
    if not 1 <= number <= len(cases):
        print("No such case.")
        return
    case = cases[number - 1]
    print(f"\nCase: {case['title']}\n  {case['claim']['description']}")
    run(ClaimRequest(**case["claim"]), source="demo")


def audit(ref: str) -> None:
    """Everything recorded about a claim, oldest first, including results that were later superseded."""
    ref = ref.strip()
    with session() as s:
        sub = s.get(Submission, ref) or next(iter(s.scalars(select(Submission).where(Submission.claim_id == ref.upper()))), None)
        if sub is None:
            return print("No such claim or submission.")
        print(f"\nSUBMISSION {sub.submission_id}  received {sub.created_at:%Y-%m-%d %H:%M:%S}  outcome: {sub.outcome}")
        print(f"  submitted: {sub.payload}")
        claim = s.get(Claim, sub.claim_id) if sub.claim_id else None
    if claim is None:
        return print("  No claim was registered.")
    print(f"\nCLAIM {claim.claim_id}  status {claim.status}  recommended {claim.recommendation}  payout {inr(claim.payout)}")
    print(f"  reviewer: {claim.reviewer_decision or '-'} {claim.reviewer_note or ''}")
    current = {f.id for f in get_findings(claim.claim_id)}
    print(f"\n{'TIME':10}{'AGENT':18}{'CHECK':26}{'STATUS':8}SUMMARY")
    for f in get_findings(claim.claim_id, history=True):
        print(f"{f.created_at:%H:%M:%S}  {f.agent:18}{f.name:26}{f.status:8}{f.summary}" + ("" if f.id in current else "  (superseded)"))


def _documents(text: str) -> list[str]:
    return DOCS_ALL if text.lower() == "all" else text.split(",")


def new_claim() -> None:
    print(f"\nKnown parts: {', '.join(PARTS)}")
    try:
        req = ClaimRequest(
            customer_id=ask("Customer ID", "CUST1001"), policy_id=ask("Policy ID", "MOT2026001"),
            incident_type=ask("Incident type (accident/theft/fire/flood/vandalism)", "accident"),
            incident_date=ask("Incident date (YYYY-MM-DD)"), reported_date=ask("Reported date", str(date.today())),
            location=ask("Location"), description=ask("What happened"),
            estimated_damage=ask("Estimated damage in rupees"),
            damaged_parts=ask("Damaged parts (comma separated, blank for none)").split(","),
            third_party_involved=ask("Third party involved? (y/n)", "n").lower() == "y",
            alcohol_involved=ask("Alcohol involved? (y/n)", "n").lower() == "y",
            driver_licence_valid=ask("Driver had a valid licence? (y/n)", "y").lower() == "y",
            documents=_documents(ask(f"Documents received (comma separated, or 'all' for the {len(DOCS_ALL)} standard ones)", "all")),
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
    parser.add_argument("--demo", type=int, help="run golden claim N")
    parser.add_argument("--eval", action="store_true", help="run the golden dataset")
    parser.add_argument("--redteam", action="store_true", help="run the adversarial claims")
    parser.add_argument("--gate", action="store_true", help="golden + red team, exit 1 if a threshold is missed")
    parser.add_argument("--audit", metavar="ID", help="show the full history of a claim or submission")
    parser.add_argument("--reset", action="store_true", help="rebuild the database from data/*.csv")
    args = parser.parse_args()

    init_db(reset=args.reset)
    if args.audit:
        return audit(args.audit)
    if args.gate:
        sys.exit(0 if gate({**run_golden(), **run_redteam()}) else 1)
    if args.eval:
        return run_golden()
    if args.redteam:
        return run_redteam()
    if args.reset:
        return print("Database reset.")
    if args.demo:
        return choose_demo(args.demo)

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
