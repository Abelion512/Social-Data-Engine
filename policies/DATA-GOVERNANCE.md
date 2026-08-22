# Data Governance Policy (engineering policy — not a legal contract)

How captured data is structured, traced, and eventually removed. No legal or
regulatory claims.

## CURRENTLY ENFORCED

- **Raw observations are preserved append-only.** `JsonlDataset` appends with
  per-batch `fsync`; records are never rewritten in place; id-dedup skips
  duplicates instead of mutating history.
- **Raw vs derived separation.** Distinct output tiers (`raw` → `curated` →
  `normalized` → `enriched`); `Content.text_raw` coexists with
  `text_normalized`; derived `Annotation` records carry their own model +
  annotation version.
- **Run-level provenance exists.** Checkpoints and `RunSummary` persist
  run/job ids, provider, target, timestamps, and `AcquisitionMetrics`.

## CURRENTLY PARTIALLY ENFORCED

- **Provenance completeness** — the canonical schema declares `Provenance`
  required at every level, but manifest writing currently lives in the legacy
  self-improvement loop, not in every acquisition writer.
- **Transformation traceability** — dedup/quality decisions are visible in
  code and tests; there is no per-record transform ledger linking an output
  row to the exact pipeline version that produced it.

## PLANNED

- Deletion and retention workflows that are explicit and auditable (right now
  deletion = manual file removal).
- Per-record provenance stamped at dataset append (collector version, policy
  model version, run id).
- Transform ledger: append-only events for every derived tier write.

## Invariant to protect

Raw stores are append-only. Any future feature that edits or rewrites raw
observations in place violates this policy and needs a constitution-amendment
discussion first.
