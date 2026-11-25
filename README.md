# InsureFlow AI

Motor-insurance claims processing by a team of Andromeda agents, run from the command line.
A customer claim goes through four specialist agents under one supervisor, ends in a
recommendation, and waits for a human reviewer to approve, reject or investigate.

```
main.py (CLI) ──► supervisor.py ──► Supervisor ─┬─► claim_agent       register the claim
                                                ├─► policy_agent      validity, coverage, exclusions, documents
                                                ├─► fraud_agent       duplicates, frequency, fraud score
                                                └─► assessment_agent  damage, depreciation, payout, recommendation
                                                         │
                                       human reviewer ◄──┘   (approve / reject / investigate)
```

## Run

```bash
pip install -r requirements.txt        # Andromeda must be importable: pip install -e <andromeda repo>
cp .env.example .env                   # add OPENAI_API_KEY
python main.py                         # menu
python main.py --demo 1                # run one of the 7 demo claims
python main.py --reset                 # fresh dummy data
```

## Demo claims

Each uses its own customer, so they do not trip the duplicate check against one another. Run one
twice and the second is correctly flagged as a duplicate of the first; use `--reset` to start over.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Clean accident claim (Creta, ₹80,000 claimed) | APPROVE, payout ₹50,450 |
| 2 | Duplicate of an already settled claim | INVESTIGATE |
| 3 | Lapsed policy | REJECT |
| 4 | Third-party-only policy: own damage not covered | REJECT |
| 5 | Four earlier claims this year, reported 9 days late | INVESTIGATE |
| 6 | Documents missing, claim 15 days after the policy started | INVESTIGATE |
| 7 | Driver had been drinking | REJECT |

## How Andromeda is used

| Concept | Where |
|---------|-------|
| **Supervisor** routing to specialists | `supervisor.py`, `route_to_agent` with `invocation_type="task"` |
| **Skills** (`skills/*/SKILL.md`) | One per agent plus `claim-orchestration` for the supervisor. Each holds the procedure. |
| **Skill-gated tools** | Tools are registered once but given to no agent directly. An agent receives them only after `load_skill`, via the skill's `allowed-tools`. |
| **Guardrails** | Supervisor only. Input: prompt injection in the claimant statement blocks the claim before anything is registered. Output: no promises of payment. |
| **Planning** | The supervisor writes and ticks its own todo list. |
| **Memory** | Per run, each agent's own message history. Across claims, the `memory` table in SQLite, shown to the Claim Agent via `get_customer`. |
| **Tracing** | Set `LANGFUSE_*` in `.env` (needs `andromeda[ops]`). Nothing else to configure. |

## Design rules

- **The model never does arithmetic.** Depreciation, deductible, payout and the fraud score are plain
  Python in the tools. The model chooses tools, reads the claimant's statement, and explains.
- **Agents pass ids, not data.** The submission is staged in SQLite; agents exchange a `claim_id` and
  read everything else themselves, so no agent re-types an amount or a date.
- **Every check is recorded** in the `findings` table: the log the reviewer sees.
- **The agents only recommend.** The final status is set by `finalize_claim` after a human decides.

## Files

```
main.py            CLI: menu, report, human review
supervisor.py      builds the Supervisor, process_claim(), finalize_claim()
agents/            one file per agent: its tools and its definition
skills/            five SKILL.md files
database.py        SQLAlchemy tables, dummy data, shared helpers
schemas.py         Pydantic: ClaimRequest, ClaimResult
config.py          model, thresholds, rupee formatting
```
