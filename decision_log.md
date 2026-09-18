# Decision Log

## Scope and safety boundary

Hospital 1 was used only as labelled development data. Hospital 4 was selected
for complete submission coverage because its contract could be represented and
tested deterministically. Hospital 2 is submitted only where every service on
an invoice has an authoritative match; unresolved invoices remain excluded.
Hospitals 3 and 5 were not attempted within the assessment time budget.

AI assistance was used and all prompts are retained in `prompts/`. For Hospital
2, the LLM handles only semantic interpretation. Contract rules, arithmetic,
rounding, totals, flags, and submission eligibility are deterministic. No
patient identifiers, invoice identifiers, or billed prices are sent to the
model.

## Shared interpretation decisions

- Money is represented as integer cents and contractual half-up rounding is
  applied after each adjustment.
- A billed price cannot override a clear textual service match because it may
  itself be erroneous. It may break a genuine textual tie only when exactly one
  contract-derived rate fits; otherwise the service remains unknown.
- Reused invoice identifiers remain structural violations. The later invoice
  occurrence is canonical, consistent with Hospital 1 labels, while line items
  are assigned to the nearest admission/discharge episode. Earlier occurrences
  remain in historical utilisation calculations.
- When a service date is both outside the contract and after the invoice date,
  the more fundamental `service_date_out_of_window` category takes precedence.

## Hospital-specific decisions

**Hospital 1.** All four non-exact expected totals are daily-cap cases. The
labels imply an unobserved quantity below the contractual maximum, which cannot
be recovered from the invoice or contract. The engine reports the auditable
maximum payable quantity rather than learning the synthetic corruption pattern;
cap-related predictions receive reduced confidence.

**Hospital 2.** The prose parser requires all 76 services and records clause
provenance plus a contract SHA-256. The contract has twelve payable cumulative
discount thresholds; a thirteenth text match is only the general ordering
definition. Ambiguous descriptions are reviewed by independent classifier and
verifier passes and accepted only when they agree, cite the selected clause,
and both reach 0.90 confidence. The live run accepted six mappings and left 81
normalised descriptions unresolved. Consequently, only 130 of 1,125 invoices
have complete pricing. Those 130 invoices are included in `submission.csv`;
the other 995 are excluded, and Hospital 2 confidence is capped at 0.90 because
there is no labelled calibration set.

**Hospital 4.** Section 11.3 prohibits repeat billing of the same service for
the same patient and date, including across invoices; later repetitions are
therefore assigned a zero expected line total. Confidence is 0.97 for a clear
correct invoice and 0.96 for a deterministic finding, with conservative caps:
0.95 for weak matches on otherwise-correct invoices, 0.94 for reused invoice
identifiers, 0.92 for unknown services, 0.88 for malformed dates, 0.84 for weak
matches on flagged lines, and 0.72 for daily-cap findings.

## Unresolved work

Hospital 2 requires human review of unresolved mappings before full coverage.
With additional time, the next sequence would be to finish that mapping review,
then implement and validate Hospital 3 and Hospital 5 independently. No totals
will be invented merely to increase coverage.
