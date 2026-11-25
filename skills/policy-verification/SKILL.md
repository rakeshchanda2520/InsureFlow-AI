---
name: policy-verification
description: Decide whether a registered motor claim falls inside its policy - validity, coverage, exclusions and required documents. Use after a claim has been registered and has a claim_id.
metadata:
  owner: underwriting-team
  version: "1"
allowed-tools: verify_policy check_coverage check_exclusions get_required_documents validate_documents
---
# Policy verification

You are given a `claim_id`. Run these in order; every tool takes the `claim_id`.

1. `verify_policy` - is the policy real, the claimant's, ACTIVE, and in force on the incident date?
2. `check_coverage` - does this policy type cover this kind of loss? A third-party-only policy never
   covers damage to the insured's own vehicle.
3. `check_exclusions` - the declared facts (alcohol, licence) are checked for you. Then **read the
   `claimant_statement` it returns**. If the statement suggests an exclusion the form missed - for
   example the car was used as a taxi, the driver had been drinking, or the damage was deliberate -
   call `check_exclusions` again with the matching key in `narrative_flags`. Only flag what the
   statement actually says; do not speculate.
4. `get_required_documents`, then `validate_documents` - which documents are missing?

Do not decide the claim. You report facts; the assessment agent turns them into a recommendation.

Reply with one line per check (PASS or FAIL and why) and the list of missing documents.
