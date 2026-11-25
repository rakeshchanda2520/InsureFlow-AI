---
name: claim-orchestration
description: End-to-end procedure for processing a newly submitted motor insurance claim with the four specialist agents. Use for every new claim submission.
metadata:
  owner: claims-operations
  version: "1"
---
# Claim orchestration

First write the four steps below as your todo list. Route each one with
`route_to_agent(agent_name, prompt, invocation_type="task")` and mark its todo complete when it finishes.
Specialists share no memory with you or each other, so every prompt must be self-contained.

1. **claim_agent** - send the submission id, customer id and policy id. It registers the claim and
   returns the `claim_id`. If it reports that the customer or policy does not exist, or the claim could
   not be registered, **stop here**: your final answer says the claim was not registered and why.
2. **policy_agent** - send the `claim_id`. It checks validity, coverage, exclusions and documents.
3. **fraud_agent** - send the `claim_id` and the `customer_id`. It checks duplicates and history and
   scores the risk.
4. **assessment_agent** - send the `claim_id` and one line each on the policy and fraud findings. It
   estimates the payout and produces the recommendation. Run it only after steps 2 and 3.

If a specialist reports an error, fix what it asked for and call it again once. Do not skip a step.

## Final answer

At most six lines: the `claim_id`, the recommendation (APPROVE, REJECT or INVESTIGATE), the estimated
payout, and the main reasons. Use the specialists' figures exactly as given; never recalculate.

You only report a recommendation. Never say a claim is approved, rejected or will be paid, and never
promise or guarantee an outcome: a human reviewer makes the decision.
