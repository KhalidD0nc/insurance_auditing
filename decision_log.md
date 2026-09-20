# Decision Log

## Scope and safety boundary

Hospital 1 was used only as labelled development data. Hospitals 2, 3, 4, and
5 now have complete invoice coverage because their contracts can be represented
and tested deterministically.

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
definition. Ambiguous descriptions were first reviewed by independent
classifier and verifier passes and accepted only when they agreed, cited the
selected clause, and both reached 0.90 confidence. The completion pass then
accepted safe token subsets, used unit basis only to break genuine textual
ties, and recorded two aggregate-rate tie-breaks for descriptions whose 50
occurrences consistently identified one documented service. Fourteen
contradictory lines remain unmapped and are reported as `unknown_service`. The
submission includes all 1,125 invoice identifiers; unknown-service rows are
capped at 0.70 confidence and all other Hospital 2 rows at 0.90 because no
labelled calibration set is available.

**Hospital 4.** Section 11.3 prohibits repeat billing of the same service for
the same patient and date, including across invoices; later repetitions are
therefore assigned a zero expected line total. Confidence is 0.97 for a clear
correct invoice and 0.96 for a deterministic finding, with conservative caps:
0.95 for weak matches on otherwise-correct invoices, 0.94 for reused invoice
identifiers, 0.92 for unknown services, 0.88 for malformed dates, 0.84 for weak
matches on flagged lines, and 0.72 for daily-cap findings.

**Hospital 3.** Amendment No. 1 overrides Appendix B from 1 January 2025 by
Service Date, not invoice date. Seven existing services receive scheduled
rates and two additional services are non-billable before that date. Six
recurring textual ties are resolved only where all occurrences consistently
use one candidate's documented basis and base, bundled, or uplifted rate.
Fourteen contradictory lines remain `unknown_service`. All 932 invoice
identifiers are submitted; unknown-service rows are capped at 0.70 confidence
and other rows at 0.90.

**Hospital 5.** The effective unit rate is calculated strictly as bundle
substitution, facility multiplier, plan-tier multiplier, premium or
non-business-day uplift, and cumulative discount, with half-up rounding after
each step. The invoice-level facility code supplies the network column because
it is the facility field present in the provided dataset. All 84 services must
appear exactly once in both multiplier tables. One recurring abbreviated
description is mapped only because all 21 occurrences have the documented
`per_visit` basis and rates reachable from Comprehensive Palliative
Consultation under the network multipliers. Twelve one-off descriptions name
services absent from Table 1 and remain `unknown_service`. All 1,050 invoice
identifiers are submitted; unknown-service rows are capped at 0.70 confidence
and all other rows at 0.90.

## Final delivery status

All scored hospitals are complete at invoice level. The final reproducibility
check passed from a fresh repository checkout: all 104 tests passed, the H2-H5
submission was rebuilt fully offline, and the regenerated 3,942-row file
matched the committed `submission.csv` byte for byte. Its SHA-256 is
`15f054e012b6526fcabe37180f91d5924d8a19fc4dff44d26eae12f29d72df95`.
No contract rate is invented for contradictory or non-contract descriptions
merely to increase apparent pricing completeness.
