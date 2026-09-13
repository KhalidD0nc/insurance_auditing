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
