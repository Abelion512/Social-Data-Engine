# Social Data Engine — Roadmap

Version 1.0 · 2026-08-22 · Derived from `PRD.md`/`SRS.md` MUST-gaps, ordered by
the security-risk ranking (`PRODUCT.md` §11) and the smallest-coherent-product
definition (`DECISIONS.md` Q5). Each phase has an explicit exit gate; phases do
not overlap their gates.

Ordering rationale: **enforcement before expansion** — no second provider, no
packaging push, and no wider agent exposure until capability evaluation and
provenance guarantees close the contract's open promises.

---

## Phase 0 — Acquisition foundation ✅ DONE (evidence, not spec)
TikTok collection (photo+video URLs, threaded replies), canonical schema,
processing pipeline, deterministic dedup/quality. PRs #1–#2; live-run #2
(44 curated, quality 0.992 mean); `scripts/trace_comment.py --trace-all`
(44/44 traced).

## Phase 1 — Bounded runtime + policy foundation ✅ DONE
Provider-independent `AcquisitionRuntime` (budgets, checkpoint/resume,
termination taxonomy, metrics) — PR #3. Constitution + policy models +
fail-closed checkpoint semantics — PR #4. Harness/Actor contract (`RunInput`,
`ActorHarness`, lifecycle, actor provenance) — **PR #5 (open, pending merge)**.
Exit gate: met by tests listed in `CURRENT-STATE.md`.

## Phase 2 — Enforcement & trust  ← NEXT (see §G of the definition report)
**Goal:** convert advisory declarations into enforced guarantees; make every
run's evidence complete and verifiable.

Scope (all MUST unless noted):
1. Policy evaluator v0 — deterministic, deny-by-default, consuming
   `CapabilityRequest` → `PolicyDecision` (FR-POL-002).
2. Gate wired into `ActorHarness.run` before runtime invocation; denials
   surface as additive `policy_denied` termination reason + summary reason
   code (FR-POL-003, FR-POL-004 design note in SRS).
3. Manifest/run-summary schema v1: version-stamped, dataset location in
   summaries, verification command replaying raw→curated per video id
   (FR-PROV-003/004, FR-DAT-003).
4. Provider-blind runtime import audit as an explicit test (NFR-007);
   preferences-source audit check (FR-PREF-001).
5. Version stamps on serialized `RunInput`/`RunSummary` (FR-INT-002) —
   additive fields riding the Phase 2 gate/summary changes.

Exit gate: evaluator table-driven tests green incl. unknown-capability→DENY and
evaluator-exception→DENY; gated run produces `policy_denied` summary without any
checkpoint side-effects; verification command passes on synthetic fixtures and
existing curated data.

## Phase 3 — Production acquisition path through the contract
**Goal:** the real browser-backed page source runs behind `TikTokAcquisitionActor`;
CLI unified onto the harness path.

Scope:
1. Wire production page source (collector/browser session, including the
   reclassified `src/harness/` tooling) into the TikTok actor's injected
   source; physical rename resolving D-011.
2. CLI/import parity for run+resume (FR-INT-001); dry-run mode (FR-HAR-004).
3. Wall-clock/network budget axes consumed (FR-ACQ-005).
4. Desktop live-test gate executed per `agents.md` §Live Test (human-in-the-loop;
   headless servers cannot satisfy this — documented constraint, not skipped).

Exit gate: one live collection meeting the stabilize criteria table
(coverage ≥95 % target, threaded replies, photo/sticker media, normalize tiers)
through the actor/harness path, recorded in `docs/VERIFICATION.md`.

## Phase 4 — Measured self-improvement v1
**Goal:** falsifiable improvement claims at lesson granularity.

Scope:
1. Lesson schema v1: observation → action → measured outcome delta →
   accepted/rejected (FR-SI-004).
2. Delta reports: baseline vs post-action metrics per actor_version
   (FR-SI-003 completion); intent-immutability explicit test (FR-LOOP-003).
3. Rejected lessons provably excluded from future strategy inputs.

Exit gate: a scripted scenario demonstrates a change claimed "improvement"
with manifest-cited deltas, and a counter-scenario where no delta ⇒ claim refused.

## Phase 5 — Pluggability proof with a second real provider
**Goal:** end-to-end proof that the platform, not just the test double, is pluggable.
Revisit trigger honored from PRD §3 LATER (requires Phase 2 done).

Scope: second platform actor + provider against the unchanged runtime;
per-version metadata distinguishability on real data; provenance chain verified.

Exit gate: J4 journey demonstrated on real data with zero runtime edits
(diff proves it), all suites green.

## Phase 6 — Agent-facing surface
**Goal:** external agents become first-class installable consumers.

Scope: packaging (`pyproject.toml`, D-012), versioned published schemas
(FR-INT-002), stable exit codes (FR-INT-003), framework-neutral agent-loop
example (FR-INT-004), secret scanner for contract payloads (FR-SEC-004).

Exit gate: fresh-environment install → agent example run → dataset+summary+manifest.

## Deferred backlog (LATER tier)
Preferences store w/ audit trail · REQUIRE_APPROVAL workflow + approval store
(Constitution §10) · cross-run strategy learning · actor registry · signing of
manifests · query/export surfaces beyond CSV experiment · run-inspection
command + agent-trace persistence (FR-TRANS-003/004) · named policy profiles
(FR-POL-005) · declared aggregate loop-budget axis (SDD §6 limitation).

## Never (OUT tier)
Scheduler/autonomous loop engine · sandbox/process isolation · dashboards/cloud ·
anti-detection-as-feature · model-training loops · bindings to named agents.
Full list: `PRODUCT.md` §8.
