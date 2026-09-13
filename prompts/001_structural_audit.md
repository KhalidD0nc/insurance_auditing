# Prompt 001 — Structural audit foundation

## Objective

Build the first reproducible layer of the invoice-auditing solution. Limit this
iteration to high-precision checks that do not depend on mapping free-text
service descriptions to contracted services.

## Requirements

- Read invoice and line-item CSVs without modifying the supplied data.
- Preserve duplicate invoice rows during ingestion and emit one finding per
  invoice ID.
- Detect duplicate invoice IDs, hospital/contract mismatches, invoice-total
  mismatches, line arithmetic errors, malformed dates, service dates outside
  the contract term, and service dates after the invoice date.
- Treat all monetary values as integer cents.
- Evaluate invoice-level flags against the Hospital 1 labels.
- Add automated tests and a reproducible command.
- Do not generate the final submission until service matching and contractual
  repricing are implemented.

## AI disclosure

An AI coding assistant was used to inspect the repository, design this first
audit layer, implement it, and write its tests. Its output was verified by
running the test suite and evaluating against the labelled development set.
