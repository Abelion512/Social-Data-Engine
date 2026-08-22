# Transparency Policy (engineering policy — not a legal contract)

Execution should be inspectable: what ran, why it stopped, where data came
from. No claims of external certification.

## CURRENTLY PARTIALLY ENFORCED

- **Termination reasons are explicit.** Every `AcquisitionRuntime` exit sets a
  raw `TerminationReason` classified into an `Outcome`; unknown reasons fail
  as `PERMANENT_FAILURE` rather than being hidden.
- **Run state is inspectable on disk.** Atomic JSON checkpoints (status,
  pagination, metrics) are committed after every batch; `RunSummary` reports
  items written/seen, termination reason, and outcome.
- **Metrics counters** — pages attempted/succeeded, dedup counts, error
  classes — are persisted with each run.
- **Failures are not silently swallowed at the runtime level** — actor
  exceptions become classified `PageResult.error` records; dataset parse
  errors during resume replay are skipped but the affected ids simply rejoin
  the run.

## CURRENTLY ENFORCED (process level)

- Live-test-before-merge requires recorded evidence in docs/verification
  notes before behavior changes land.

## PLANNED

- **Structured audit events** for individual actions and policy decisions
  (append-only JSONL co-located with run outputs).
- **Policy version stamping** — every audit event records which policy
  version evaluated it (`POLICY_MODEL_VERSION` exists; event wiring does not).
- **Decision transparency** — ALLOW/DENY/REQUIRE_APPROVAL outcomes recorded
  with the matched rule id.
- **External inspectability** — a way to query run state without reading
  checkpoint files by hand.

## Rule

No code path may hide a failure by converting it into an empty success.
Empty results must be distinguishable from suppressed errors.
