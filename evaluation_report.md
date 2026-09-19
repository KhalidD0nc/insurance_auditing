# Evaluation Report

## Scope and method

Hospital 1 is the labelled development set. The implementation parses its
contract, normalises free-text service descriptions, and applies contractual
bundles, premiums, weekend uplifts, cumulative discounts, caps, exclusions,
and rounding in the stated order. All monetary calculations use integer cents.
Hospital 1 was used for development and calibration only; it is not included in
`submission.csv`.

AI assistance was used to help develop the repository and prompts. For
Hospital 2, the `z-ai/glm-5.3-flash` model performed classifier and verifier
passes for semantic service matching, with OpenRouter used only as the API
provider. A later offline completion pass resolved safe abbreviations and
unit-basis ties and retained contradictory descriptions as unknown. Contract
extraction, validation, pricing, totals, findings, and submission decisions
remain deterministic. The prompts are versioned in `prompts/`.

## Results

The audit classified all 913 Hospital 1 invoice identifiers correctly: 58 true
positives, 855 true negatives, no false positives, and no false negatives.
Invoice-level precision, recall, and F1 are therefore 1.00. Error-category
classification was also exact for every labelled category.

Expected totals matched exactly for 909 of 913 invoices. The four differences
were all daily-cap cases and account for 304,475 total absolute-error cents
across the full development set.

| Error category | Positive cases | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `bundle_not_applied` | 5 | 1.00 | 1.00 | 1.00 |
| `contract_number_mismatch` | 5 | 1.00 | 1.00 | 1.00 |
| `cross_invoice_duplicate` | 4 | 1.00 | 1.00 | 1.00 |
| `daily_cap_exceeded` | 4 | 1.00 | 1.00 | 1.00 |
| `duplicate_invoice_id` | 5 | 1.00 | 1.00 | 1.00 |
| `exclusion_window_violation` | 4 | 1.00 | 1.00 | 1.00 |
| `invoice_total_mismatch` | 6 | 1.00 | 1.00 | 1.00 |
| `line_total_arithmetic` | 6 | 1.00 | 1.00 | 1.00 |
| `malformed_service_date` | 6 | 1.00 | 1.00 | 1.00 |
| `premium_incorrectly_applied` | 6 | 1.00 | 1.00 | 1.00 |
| `premium_omitted` | 3 | 1.00 | 1.00 | 1.00 |
| `service_date_after_invoice_date` | 5 | 1.00 | 1.00 | 1.00 |
| `service_date_out_of_window` | 5 | 1.00 | 1.00 | 1.00 |
| `unit_price_mismatch` | 10 | 1.00 | 1.00 | 1.00 |
| `unknown_service` | 12 | 1.00 | 1.00 | 1.00 |
| `volume_discount_incorrectly_applied` | 4 | 1.00 | 1.00 | 1.00 |
| `volume_discount_omitted` | 4 | 1.00 | 1.00 | 1.00 |
| `wrong_unit_basis` | 11 | 1.00 | 1.00 | 1.00 |

## Error analysis

1. **Daily caps do not reveal the intended original quantity.** The engine can
   calculate the maximum contractually payable quantity, but it cannot infer
   how many units should have been billed before corruption. This caused all
   four expected-total differences: `INV-H1-000015`, `INV-H1-000049`,
   `INV-H1-000227`, and `INV-H1-000725`. The predictions were respectively
   14,775, 25,425, 76,275, and 188,000 cents away from the labels. Cap-related
   submission rows therefore receive reduced confidence.

2. **Unknown descriptions cannot be safely repriced.** An unrecognised service
   is flagged rather than assigned an invented contract rate. For example,
   `INV-H1-000036` contains both an unknown service and a malformed date. This
   policy reproduced the labelled flag, but an unknown line could conceal an
   additional pricing error. For full-coverage unlabelled submissions, the
   calculated billed amount is preserved for that line and confidence is
   reduced rather than inventing a replacement rate.

3. **Free-text matching can be genuinely ambiguous.** Billed price is not
   allowed to override a clear text match because the price may be the error.
   It is used only to break a true textual tie when exactly one contractual
   rate fits. Otherwise the line remains unknown. `INV-H1-000132`, which
   combines a bundle issue with an unknown description, illustrates the risk
   of adjustments depending on uncertain service identity.

4. **Duplicate invoice identifiers require episode assignment.** Line items
   for a reused identifier are assigned to the nearest admission/discharge
   episode, while both occurrences remain in cumulative calculations.
   `INV-H1-000068` is a representative duplicate identifier. The development
   labels support the current later-occurrence policy, but overlapping or
   equally plausible episodes could make ownership uncertain on another
   hospital.

## Submission scope

`submission.csv` contains all 835 Hospital 4 invoice identifiers and all 1,125
Hospital 2 identifiers. Hospital 2 has 76 flagged invoices. Fourteen
contradictory descriptions across 13 invoices remain `unknown_service` with a
0.70 confidence cap; the other rows are capped at 0.90. Hospitals 3 and 5 were
not attempted within the initial time budget and are the next implementation
targets.
