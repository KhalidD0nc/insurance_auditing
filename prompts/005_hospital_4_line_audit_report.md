# Prompt 005 — Hospital 4 line-level audit report

## Objective

Produce a reproducible, inspectable Hospital 4 review report before assigning
submission confidence or writing final predictions.

## Requirements

- Add a CLI command that audits Hospital 4 with its parsed contract and prints
  JSON without mutating source data or `submission.csv`.
- Include invoice totals, differences and all invoice-level categories.
- Include every line of a reported invoice, with service-match confidence,
  billed and contractual units, quantities, caps, thresholds, cumulative
  utilisation and each rounded rate-calculation step.
- Link lines participating in bundles, exclusion windows, daily-cap groups and
  duplicate-service findings.
- Report only flagged invoices by default, with an option to include correct
  invoices.
- Clearly label the output as preliminary because Hospital 4 has no labels.
- Preserve the complete Hospital 1 regression result.

## AI disclosure

An AI coding assistant was used to design the audit-detail model, expose
cross-line evidence, implement JSON report generation and CLI wiring, and add
synthetic and real-data regression tests. No submission rows were generated.
