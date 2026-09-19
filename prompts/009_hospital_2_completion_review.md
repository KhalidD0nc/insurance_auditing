# Hospital 2 completion review

Complete Hospital 2 without weakening the audit boundary.

- Treat the contract as the authority for service identity, basis, rate, and
  adjustment order.
- Accept reordered or abbreviated descriptions only when their normalized
  tokens identify one contract service with a clear margin.
- For a genuine textual tie, use the billed unit basis only when it identifies
  exactly one tied candidate and every description token occurs in that
  candidate.
- Do not use a billed price to override a clear textual match.
- For a description that remains tied across both text and unit basis, accept
  an aggregate-rate mapping only when all occurrences consistently identify
  one documented base or bundle rate. Record the observation and clause.
- Leave semantically contradictory one-off descriptions unresolved. Report
  them as `unknown_service`; do not invent a replacement rate.
- Produce one submission row for every Hospital 2 invoice. Preserve the
  arithmetically calculated billed amount for an unknown line and reduce that
  invoice's confidence.
- Keep all contract arithmetic, rounding, totals, and final flags
  deterministic and offline.
