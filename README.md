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

**Step-by-step running and testing, with ready-made test claims: see [RUN.md](RUN.md).**

## Set up on any laptop

The dummy data is in git (`data/*.csv`). The SQLite database is built from it, so every laptop gets the same data.

```bash
git clone <repo> && cd insureflow-ai
pip install -r requirements.txt          # plus Andromeda: pip install -e <andromeda repo>
cp .env.example .env                     # then add OPENAI_API_KEY (and the Langfuse keys, see below)
python main.py --reset                   # builds data/insureflow.db from the CSVs
python main.py --eval                    # runs the 17 golden claims; expect "17/17 cases passed"
python main.py                           # the menu
```

The database also builds itself on first run, so `--reset` is only needed to start over. It is not in git
(`data/*.db`), because it changes with every claim; the CSVs are the data. To change the dummy data, edit
`data_generator.py` and run `python data_generator.py`, then commit `data/`; other laptops pull and `--reset`.

| File in `data/` | Holds |
|-----------------|-------|
| `customers.csv` `vehicles.csv` `policies.csv` | 12 customers with Indian vehicles, IDV and policies (one lapsed, one third-party-only) |
| `claims.csv` `memory.csv` | 13 earlier claims and the notes remembered about two customers |
| `golden_claims.json` | the 17 golden submissions, each with the recommendation, payout and fraud level it must produce |
| `redteam.json` | the 12 adversarial claims, hand-written |
| `parts.csv` `depreciation.csv` | the Assessment Agent's price list and depreciation bands. Edited by hand, not generated: change a price or a band here and the payout changes. |

## Golden claims

Seventeen submissions with known answers: seven basic ones (`--demo 1` to `7`) and ten multi-factor ones (`8` to `17`).
Each runs on freshly loaded data. The full list, with what each is expected to produce, is in [Evaluation.md](Evaluation.md).

| Command | Does |
|---------|------|
| `python main.py --eval` | the golden dataset: recommendation, payout and fraud level compared with the expected ones |
| `python main.py --redteam` | six security controls, then twelve attacks; each is graded by code, not by a model |
| `python main.py --gate` | both, then the thresholds (accuracy 90%, red team 100%, latency p95 under the budget); exit code 1 if missed |
| `python main.py --audit <id>` | everything recorded about a claim, oldest first |

## Observability: Langfuse tracing and evaluation

Create a Langfuse project (cloud or self-hosted), put its three keys in `.env`, and everything below switches on.
With the keys blank it all does nothing.

**Tracing.** One claim is one trace (`thread_id` is the submission id), filterable by customer (`user_id`) and by
tag (`demo`, `eval`, `redteam`, `manual`). Andromeda records every agent, model, tool and skill load inside it. The
application adds what happens around the agents, as events on the same trace:

| Event | When |
|-------|------|
| `guardrail-blocked` (WARNING) | the input or output guardrail stopped a claim |
| `human-review` | the reviewer decided: their decision, what the agents said, whether they agreed |
| `pipeline-incomplete` (ERROR) | the agents did not reach a recommendation |

**Evaluation.** Scores are attached to each agent and appear on the trace as `<agent>:<score>`. They are checks of
rules (free, exact), an LLM judge on its own model, a human, and a golden dataset. **[Evaluation.md](Evaluation.md) lists
every score, how each of the 17 claims is judged, and how it all appears in Langfuse.** All of it is in `observability.py`
and `evals.py`; each agent makes one `observe(...)` call. The judges cost a call per agent run, so
`INSUREFLOW_JUDGE_SAMPLE=0.2` judges a fifth of them, on `INSUREFLOW_JUDGE_MODEL`.
Traces contain the claimant's statement (it is the supervisor's input), with emails and phone numbers masked.

## How Andromeda is used

| Concept | Where |
|---------|-------|
| **Supervisor** routing to specialists | `supervisor.py`, `route_to_agent` with `invocation_type="task"` |
| **Skills** (`skills/*/SKILL.md`) | One per agent plus `claim-orchestration` for the supervisor. Each holds the procedure. |
| **Skill-gated tools** | Tools are registered once but given to no agent directly. An agent receives them only after `load_skill`, via the skill's `allowed-tools`. |
| **Guardrails** | Supervisor: input (prompt injection blocks the claim before anything is registered) and output (no promises of payment). Specialists: tool results, against text a customer or reviewer once wrote. All of it, and the other layers, in `security.py`. |
| **Planning** | The supervisor writes and ticks its own todo list. |
| **Memory** | Within a run, each agent's own message history. Across claims, the `memory` table in SQLite, shown to the Claim Agent via `get_customer`. |

## Design rules

- **The model never does arithmetic.** Depreciation, deductible, payout and the fraud score are plain
  Python in the tools. The model chooses tools, reads the claimant's statement, and explains.
- **Agents pass ids, not data.** The submission is staged in SQLite; agents exchange a `claim_id` and
  read everything else themselves, so no agent re-types an amount or a date.
- **Every check is recorded** in the `findings` table: the log the reviewer sees.
- **The agents only recommend.** The final status is set by `finalize_claim` after a human decides.
- **Tools are scoped to the claim.** An agent can read only the customer and policy its submission names, and can correct a
  description or location but never the claimed amount.
- **The record is append-only.** A check that is run again adds a result; `--audit` shows both.

## Files

```
main.py            CLI: menu, report, human review, --eval, --redteam, --gate, --audit
supervisor.py      builds the Supervisor, process_claim(), finalize_claim()
observability.py   Langfuse: trace attributes, evaluators, scoring
evals.py           golden dataset, red team, quality gate, the reviewer-to-golden flywheel
security.py        the defence layers: prompt rules, guardrails, masking
agents/            one file per agent: its tools and its definition
skills/            five SKILL.md files
Evaluation.md      how every query is evaluated, and how it shows in Langfuse
database.py        SQLAlchemy tables, CSV loader, shared helpers
data_generator.py  writes data/*.csv and golden_claims.json (run once, commit the output)
schemas.py         Pydantic: ClaimRequest, ClaimResult
config.py          model, thresholds, rupee formatting
data/              the dummy data, tracked in git
```
