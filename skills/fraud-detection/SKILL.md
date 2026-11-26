---
name: fraud-detection
description: Check a registered motor claim for duplicates, unusual claim frequency and other fraud indicators, and produce a fraud report. Use after a claim has been registered and has a claim_id.
metadata:
  owner: special-investigation-unit
  version: "1"
allowed-tools: get_claim_history detect_duplicate check_claim_frequency calculate_fraud_score generate_fraud_report
---
# Fraud detection

You are given a `claim_id`. Run these in order.

1. `get_claim_history(claim_id)` - the earlier claims by the customer behind this one.
2. `detect_duplicate(claim_id)` - another claim on the same vehicle or by the same customer within a
   few days of this incident.
3. `check_claim_frequency(claim_id)` - claims in the last 12 months.
4. `calculate_fraud_score(claim_id)` - the 0-100 score and its indicators. **Read the
   `claimant_statement` and `damaged_parts` it returns.**
5. `generate_fraud_report(claim_id, analyst_note)` - the final report. In `analyst_note` write one or
   two sentences on whether the statement is consistent with the damaged parts and the incident type.
   Do not repeat the indicators, which the report already lists.

A high score is a reason for a human to look closer, not proof of fraud. Never accuse the customer.

Reply with the score, the level (low / medium / high) and each indicator.
