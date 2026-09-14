# Prompt 006 — Hospital 4 final submission rows

## Objective

Review Hospital 4 ambiguity classes, calibrate confidence honestly and write one
submission row for every Hospital 4 invoice identifier.

## Requirements

- Review unknown-service lines, generic price mismatches, weak service matches,
  malformed dates, daily caps and compound findings using the line-level report.
- Use Hospital 1 labels only for transfer calibration; do not claim measured
  Hospital 4 accuracy.
- Preserve billed value for an unrecognised service rather than inventing a
  contract rate.
- Reduce confidence for amount-ambiguous daily caps, malformed dates, weak
  service identity and reused invoice identifiers.
- Emit the exact submission-template columns with integer cent values and a
  confidence between zero and one.
- Replace Hospital 4 rows atomically while preserving rows for other hospitals.
- Verify complete, unique coverage and retain the Hospital 1 regression result.

## AI disclosure

An AI coding assistant was used to review uncertainty classes, compare them with
Hospital 1 development behaviour, implement confidence calibration, add atomic
CSV generation and validate the resulting Hospital 4 rows.
