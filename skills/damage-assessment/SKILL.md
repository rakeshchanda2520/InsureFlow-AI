---
name: damage-assessment
description: Estimate repair cost, depreciation, deductible and payout for a motor claim and generate the approve / reject / investigate recommendation. Use after the policy and fraud checks are complete.
metadata:
  owner: claims-assessment-team
  version: "1"
allowed-tools: estimate_damage calculate_depreciation calculate_deductible calculate_payout generate_decision
---
# Damage assessment

You are given a `claim_id` and a summary of the policy and fraud findings. Run these in order.

1. `estimate_damage` - repair cost of the damaged parts against the amount claimed.
2. `calculate_depreciation` - depreciation on replaced parts, by vehicle age and policy add-ons.
3. `calculate_deductible` - what the customer bears.
4. `calculate_payout` - assessed cost less depreciation and deductible, capped at the insured value.
5. `generate_decision(claim_id, rationale)` - the recommendation. Write `rationale` as two sentences
   using only figures the tools returned.

Never calculate money yourself and never quote a figure a tool did not give you.

If `generate_decision` says checks are missing, do not guess: reply that the policy and fraud checks
must be run first, and say which ones.

You only recommend. You never approve, reject or promise a payment: a human reviewer decides.

Reply with the recommendation, the estimated payout and the reasons.
