# Hospital 5 service-description review

Review recurring descriptions that deterministic conservative matching cannot
resolve. Accept a mapping only when the normalized text, unit basis, and every
observed billed rate are compatible with one contract service after applying
the documented facility and plan-tier multipliers. Rates are corroborating
evidence and must not override contradictory service text.

The recurring description `comprehensive consultation` is accepted as
`Comprehensive Palliative Consultation`: all 21 occurrences use `per_visit`,
and every observed rate is produced by that service's base rate and documented
network multipliers.

The twelve one-off descriptions naming services absent from Table 1 remain
unresolved. They must be reported as `unknown_service`; no nearest contract
service may be invented for them.
