# Decision Log

## Scope

This log records decisions that materially affect invoice predictions. It is
updated as each hospital is implemented.

## Hospital 1 development decisions

### Duplicate invoice identifiers

The invoice CSV can contain more than one physical invoice with the same
`invoice_id`, while the submission and labels contain one row per identifier.
Hospital 1 labels consistently treat the later CSV occurrence as the invoice
whose expected total is reported; the earlier occurrence establishes that the
identifier was already used. The implementation therefore emits one result for
the later occurrence and flags `duplicate_invoice_id`.

Line items sharing a duplicated identifier are assigned to the nearest invoice
episode using service date, admission date and discharge date. Items outside an
episode are assigned to the nearest episode rather than discarded. Both invoice
occurrences remain in historical calculations such as cumulative utilisation.

### Overlapping date violations

A service date outside the contract term may also be later than the invoice
date. Hospital 1 labels report the more fundamental
`service_date_out_of_window` category in this case. The implementation suppresses
the derivative `service_date_after_invoice_date` label while still flagging the
invoice.

### Daily-cap expected totals

For all four Hospital 1 `daily_cap_exceeded` cases, the labelled expected total
implies a quantity below the contractual cap, not merely removal of units above
the cap. For example, one line bills nine tests against a four-test cap, while
the label implies three tests. The unobserved pre-error quantity cannot be
recovered from the contract or invoice.

The engine therefore uses the maximum contractually billable quantity as the
auditable expected quantity. It does not learn a correction from the four
development labels because that would encode the synthetic corruption process
rather than contract logic. Predictions involving a cap will receive reduced
confidence, and this limitation will be disclosed in the evaluation report.

### Description matching and billed prices

Service descriptions are matched from normalised words and common billing
abbreviations. A billed price cannot override a clear textual match because the
price may itself be erroneous. It may break a genuine textual tie only when it
matches exactly one rate obtainable from the contract, including a stated
premium, discount or bundled rate. Otherwise the service is left unknown.

## Hospital 4 implementation decisions

### Duplicate service billing

Section 11.3 prohibits billing the same Service more than once for the same
Patient and Service Date, whether the repetition occurs inside one invoice or
across invoices. Hospital 4 therefore uses the
`repeated_service_per_patient_day` policy. After service descriptions are
normalised, occurrences are ordered by line identifier; the first remains
payable and every later occurrence is assigned `duplicate_service` and a zero
expected line total.

Hospital 1 retains its development-calibrated policy, which only identifies a
matching quantity and unit basis repeated across invoices. The difference is
represented as contract data rather than a hospital-number condition in the
pricing engine.

After line items belonging to reused invoice identifiers are assigned to their
correct invoice occurrence, the Hospital 4 dataset contains five repeated
patient/date/service groups. They affect four later invoice identifiers because
two repeated Services occur on one invoice. All five repetitions are across
invoices and have matching quantities, unit bases and rates. No same-invoice
repeated group was found, but that contract path is covered by a synthetic
regression test.

### Hospital 4 confidence calibration

Hospital 4 has no labels, so confidence is transferred conservatively from the
Hospital 1 development evidence rather than presented as measured Hospital 4
accuracy. The base confidence is 0.97 for an unflagged invoice and 0.96 for a
deterministic finding. Hospital 1 reproduced all twelve `unknown_service`
expected totals by retaining the billed rate for the unrecognised line, so that
category is capped at 0.92 rather than assigned an invented contract rate.

Malformed dates are capped at 0.88 because date-dependent adjustments cannot be
fully reconstructed. A flagged line whose identity required a weak textual
match is capped at 0.84; an otherwise-correct invoice containing such a match is
capped at 0.95. Reused invoice identifiers are capped at 0.94 because line-item
ownership is resolved heuristically.

Daily-cap findings are capped at 0.72. They were the only systematic Hospital 1
amount ambiguity: all four flags were correct, but none of their labelled totals
equalled the maximum contractually payable quantity. The Hospital 4 submission
therefore reports the auditable contractual maximum and exposes the uncertainty
through confidence instead of learning the hidden corruption quantity.
