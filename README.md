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

## Set up on any laptop

The dummy data is in git (`data/*.csv`). The SQLite database is built from it, so every laptop gets the same data.

```bash
git clone <repo> && cd insureflow-ai
pip install -r requirements.txt          # plus Andromeda: pip install -e <andromeda repo>
cp .env.example .env                     # then add OPENAI_API_KEY (and the Langfuse keys, see below)
python main.py --reset                   # builds data/insureflow.db from the CSVs
python main.py --eval                    # runs all 7 demo claims; expect "7/7 scenarios passed"
python main.py                           # the menu
```

The database also builds itself on first run, so `--reset` is only needed to start over. It is not in git
(`data/*.db`), because it changes with every claim; the CSVs are the data. To change the dummy data, edit
`data_generator.py` and run `python data_generator.py`, then commit `data/`; other laptops pull and `--reset`.

| File in `data/` | Holds |
|-----------------|-------|
| `customers.csv` `vehicles.csv` `policies.csv` | 12 customers with Indian vehicles, IDV and policies (one lapsed, one third-party-only) |
| `claims.csv` `memory.csv` | 13 earlier claims and the notes remembered about two customers |
| `demo_claims.json` | the 7 demo submissions and the recommendation each must produce |
| `parts.csv` `depreciation.csv` | the Assessment Agent's price list and depreciation bands. Edited by hand, not generated: change a price or a band here and the payout changes. |

## Demo claims

Each uses its own customer. Run one twice and the second is flagged as a duplicate; use `--reset`.

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Clean accident claim (Creta, ₹80,000 claimed) | APPROVE, payout ₹50,450 |
| 2 | Duplicate of an already settled claim | INVESTIGATE |
| 3 | Lapsed policy | REJECT |
| 4 | Third-party-only policy: own damage not covered | REJECT |
| 5 | Four earlier claims this year, reported 9 days late | INVESTIGATE |
| 6 | Documents missing, claim 15 days after the policy started | INVESTIGATE |
| 7 | Driver had been drinking | REJECT |

## Observability: Langfuse tracing and evaluation

Create a Langfuse project (cloud or self-hosted), put its three keys in `.env`, and everything below switches on.
With the keys blank it all does nothing.

**Tracing.** One claim is one trace (`thread_id` is the submission id), filterable by customer (`user_id`) and by
tag (`demo`, `eval`, `manual`). Andromeda records every agent, model, tool and skill load inside it. The
application adds what happens around the agents, as events on the same trace:

| Event | When |
|-------|------|
| `guardrail-blocked` (WARNING) | the input or output guardrail stopped a claim |
| `human-review` | the reviewer decided: their decision, what the agents said, whether they agreed |
| `pipeline-incomplete` (ERROR) | the agents did not reach a recommendation |

**Evaluation.** Scores are attached to each agent and appear on the trace as `<agent>:<score>`.

| Score | On | Checks | Kind |
|-------|----|--------|------|
| `skill_first` | every agent | the first tool called was `load_skill` | rule |
| `tool_usage` | every agent | tools were used and returned results | built-in |
| `grounded` | every agent | every ₹ amount and claim id in the reply appears in a tool result: nothing invented | rule |
| `hallucination` | every agent | the reply stays faithful to its tool results | LLM judge |
| `pipeline_complete` | supervisor | all four specialists were routed to | rule |
| `answer_matches_decision` | supervisor | the answer states the recommendation actually recorded | rule |
| `human_agreement` | the claim | 1 if the reviewer agreed with the agents | human |
| `scenario_correct` | the claim | `--eval`: the demo produced its expected recommendation | dataset |

Rules are free and exact; the one LLM judge costs a call per agent run, so `INSUREFLOW_JUDGE_SAMPLE=0.2` judges
a fifth of them. All of it is in `observability.py`; each agent makes one `observe(...)` call.
Traces contain the claimant's statement (it is the supervisor's input): mask or drop it before sending real claims.

## How Andromeda is used

| Concept | Where |
|---------|-------|
| **Supervisor** routing to specialists | `supervisor.py`, `route_to_agent` with `invocation_type="task"` |
| **Skills** (`skills/*/SKILL.md`) | One per agent plus `claim-orchestration` for the supervisor. Each holds the procedure. |
| **Skill-gated tools** | Tools are registered once but given to no agent directly. An agent receives them only after `load_skill`, via the skill's `allowed-tools`. |
| **Guardrails** | Supervisor only. Input: prompt injection in the claimant statement blocks the claim before anything is registered. Output: no promises of payment. |
| **Planning** | The supervisor writes and ticks its own todo list. |
| **Memory** | Within a run, each agent's own message history. Across claims, the `memory` table in SQLite, shown to the Claim Agent via `get_customer`. |

## Design rules

- **The model never does arithmetic.** Depreciation, deductible, payout and the fraud score are plain
  Python in the tools. The model chooses tools, reads the claimant's statement, and explains.
- **Agents pass ids, not data.** The submission is staged in SQLite; agents exchange a `claim_id` and
  read everything else themselves, so no agent re-types an amount or a date.
- **Every check is recorded** in the `findings` table: the log the reviewer sees.
- **The agents only recommend.** The final status is set by `finalize_claim` after a human decides.

## Files

```
main.py            CLI: menu, report, human review, --eval
supervisor.py      builds the Supervisor, process_claim(), finalize_claim()
observability.py   Langfuse: trace attributes, evaluators, scoring
agents/            one file per agent: its tools and its definition
skills/            five SKILL.md files
database.py        SQLAlchemy tables, CSV loader, shared helpers
data_generator.py  writes data/*.csv and demo_claims.json (run once, commit the output)
schemas.py         Pydantic: ClaimRequest, ClaimResult
config.py          model, thresholds, rupee formatting
data/              the dummy data, tracked in git
```
