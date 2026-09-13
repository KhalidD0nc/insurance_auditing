# Prompt 002 — Hospital 1 contract audit

## Objective

Use the labelled Hospital 1 development set to test the complete contract-audit
approach before applying it to unlabelled hospitals.

## Requirements

- Parse rates, unit bases, caps, premiums, weekend uplifts, cumulative volume
  discounts, bundles and exclusion windows from the supplied Markdown contract.
- Match reordered and abbreviated billing descriptions conservatively; reject
  genuinely conflicting or incomplete identities.
- Use a billed rate only to resolve an otherwise tied description, never to
  override a clear textual match.
- Apply half-up rounding after every contractual adjustment.
- Preserve full historical utilisation when calculating cumulative discounts,
  including earlier occurrences of a duplicated invoice identifier.
- Evaluate invoice flags, expected totals and each error category against the
  Hospital 1 labels.
- Record irreducible ambiguities instead of inventing certainty.

## AI disclosure

An AI coding assistant was used to extract the contract structure, develop the
normalisation vocabulary, implement the deterministic pricing engine, diagnose
development-set failures and add regression tests. All reported results come
from executable code run against the supplied dataset.
