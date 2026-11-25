---
name: claim-intake
description: Look up the customer and policy behind a submitted motor claim and register it to obtain a claim_id. Use when a new claim submission needs to be registered.
metadata:
  owner: claims-team
  version: "1"
allowed-tools: search_customer get_customer get_policy create_claim update_claim get_claim_status
---
# Claim intake

You are given a submission id, a customer id and a policy id.

1. Call `get_customer(customer_id)`. If it is not found, try `search_customer` with any name, phone
   number or registration you were given. If the customer still cannot be found, **stop**: do not
   register anything and report that the customer is unknown.
2. Call `get_policy(policy_id)`. If the policy does not exist, **stop** and report it.
3. Read the customer's `memory` notes. They record earlier settled claims and fraud flags and matter
   to the agents after you.
4. Call `create_claim(submission_id)`. This returns the `claim_id`. Never invent or guess a claim id.
5. If the claim needs a correction (a typo in the location, for example), use `update_claim`.

Reply with: the `claim_id`, the `customer_id`, the customer's name, the vehicle, the policy type and
status, and any memory notes word for word.
