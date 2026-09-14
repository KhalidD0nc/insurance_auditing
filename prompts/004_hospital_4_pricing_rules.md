# Prompt 004 — Hospital 4 pricing rules

## Objective

Make the generic contract auditor execute Hospital 4 rules correctly without
producing a final submission.

## Requirements

- Represent duplicate billing policy as contract data rather than branching on
  hospital number.
- Preserve Hospital 1's labelled duplicate behaviour.
- For Hospital 4, treat every occurrence after the first canonical Service for
  a Patient and Service Date as non-payable, inside or across invoices.
- Verify bundle substitution across invoices, aggregate threshold premiums,
  cumulative discount boundaries, daily caps across invoices, bidirectional
  exclusion windows and half-up rounding after each adjustment.
- Run the complete regression suite and confirm Hospital 1 remains unchanged.
- Do not write `submission.csv` or claim labelled Hospital 4 accuracy.

## AI disclosure

An AI coding assistant was used to model Hospital 4's duplicate-billing policy,
inspect repeated service groups in the supplied data, implement the policy in
the generic auditor and add deterministic pricing tests.
