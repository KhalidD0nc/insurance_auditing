# Prompt 003 — Hospital 4 contract parser

## Objective

Represent the Hospital 4 conditional reimbursement agreement as validated,
deterministic `ContractRules` without auditing invoices or producing predictions.

## Requirements

- Parse contract identity, effective dates, base rates and unit bases.
- Parse threshold premiums, daily caps, bundled rates, cumulative discounts and
  exclusion windows from their Hospital 4 section layout.
- Preserve the contract's adjustment order and half-up rounding convention.
- Confirm that facility and plan tier do not modify Hospital 4 rates and that no
  non-business-day uplift is defined.
- Reject unsupported metadata, incomplete tables, duplicate rules and references
  to services absent from the base-rate schedule.
- Add representative extraction tests and retain the complete Hospital 1
  regression result.

## AI disclosure

An AI coding assistant was used to inspect the contract structure, implement the
Hospital 4 parser and validation checks, and add automated extraction tests. No
Hospital 4 invoice predictions were generated in this phase.
