# Social Data Engine — Roadmap

Version 1.0 · 2026-08-22 · Derived from `PRD.md`/`SRS.md` MUST-gaps, ordered by
the security-risk ranking (`PRODUCT.md` §11) and the smallest-coherent-product
definition (`DECISIONS.md` Q5). Each phase has an explicit exit gate; phases do
not overlap their gates.

Ordering rationale: **enforcement before expansion** — no second provider, no
packaging push, and no wider agent exposure until capability evaluation and
provenance guarantees close the contract's open promises.

**Security overlay (PR #8).** Autonomy increases are additionally gated by the
security maturity ladder (`docs/SECURITY-THREAT-MODEL.md` §1,
`docs/architecture/CONTAINMENT.md`, `DECISIONS.md` D-014): a phase completes
only if its S-gates are green, and **no exposure widening happens before
S-G1 + S-G2 land.** The next implementation PR is pinned: **PR #9 = S-G2 +
gate-default + ceiling clamps + secret-scanner stub** (threat model §3.D).

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
6. **S-G2 (security):** identity-safe identifiers (slug charset for
   `job_id`/`run_id`/`video_id`) + workspace-rooted path resolution in
   `RunContext.create` / improve-manifest builders — closes P0 TM-01/TM-13.
7. **S-G1 part 1 (security):** harness installs a deny-all profile by default;
   trusted-operator mode becomes an explicit named switch (removed at Phase 3
   CLI unification).
8. **S-G4 (security):** profile ceiling clamps on budget axes (caller may
   lower, never exceed) — closes TM-04's escalation half.

Exit gate: evaluator table-driven tests green incl. unknown-capability→DENY and
evaluator-exception→DENY; gated run produces `policy_denied` summary without any
checkpoint side-effects; verification command passes on synthetic fixtures and
existing curated data; **plus security exit:** traversal-id and rooted-path
tests prove hostile identifiers cannot escape `state_dir`/`data_dir`, and a
profile-less harness run denies by default (D-014 rule 2).

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

## Security gates (S-gates) — cross-phase overlay

Owned by the ladder in `docs/SECURITY-THREAT-MODEL.md` §1; each gate cites its
closed threats (TM-xx). A phase's exit requires its scheduled gates.

| Gate | Scope | Closes | Scheduled |
|---|---|---|---|
| S-G1 | Mandatory deny-by-default gate on every sanctioned entry point; import audit; CLI unified behind harness | TM-02, TM-24 | Phase 2 (part 1) → Phase 3 (CLI) |
| S-G2 | Identity-safe identifiers + workspace-rooted path resolution | TM-01, TM-13 (**P0**) | Phase 2 / PR #9 |
| S-G3 | Action↔capability linkage at engine insertion points 2 & 4 | TM-03, TM-23 | Phase 2→3 |
| S-G4 | Profile ceiling clamps on budget axes | TM-04 | Phase 2 / PR #9 |
| S-G5 | Checkpoint↔actor binding on resume + single-writer lock + per-actor state namespaces | TM-07, TM-08 | Phase 3 |
| S-G6 | Filesystem jail for all writers (symlink-escape refusal) | §2.5 residual | Phase 3 |
| S-G7 | Egress allowlist + dedicated automation profile + route-pattern scoping + log redaction | TM-17, TM-18, TM-20 | Phase 3→4 |
| S-G8 | Mechanical secret scanner over contract payloads (pulled forward from Phase 6) | TM-05, TM-20, TM-21 | stub Phase 2/PR #9, full Phase 3 |
| S-G9 | Out-of-process actor boundary + IPC contract tests ⚠ requires amending PRODUCT.md §8 first | TM-03 residual | L4 only |
| S-G10 | Wall-clock/network budget axes consumed pre-dispatch; kill-switch verb | TM-06 | Phase 3 |
| S-G11 | Sub-agent grant attenuation proof (child ⊆ parent ∩ profile) | threat model §2.13 | L5 only |
| S-G12 | Aggregate budget axis across loops/agents | TM-04 residual | Phase 4→L5 |
| S-G13 | Evidence integrity: hash-chained (later HMAC) manifests/checkpoints; forged-state refusal | TM-14 | Phase 4 |
| S-G14 | Lesson-integrity: accept/reject verdicts; captured data treated as untrusted before cross-run learning | TM-15, TM-16 | Phase 4 (blocks cross-run learning exit) |

## Deferred backlog (LATER tier)
Preferences store w/ audit trail · REQUIRE_APPROVAL workflow + approval store
(Constitution §10) · cross-run strategy learning · actor registry · signing of
manifests · query/export surfaces beyond CSV experiment · run-inspection
command + agent-trace persistence (FR-TRANS-003/004) · named policy profiles
(FR-POL-005) · declared aggregate loop-budget axis (SDD §6 limitation) ·
doc/code drift reconciliation for merged evaluator work (TM-25: refresh
CURRENT-STATE/SRS labels citing `tests/test_policy_evaluator.py`).

## Never (OUT tier)
Scheduler/autonomous loop engine · sandbox/process isolation · dashboards/cloud ·
anti-detection-as-feature · model-training loops · bindings to named agents.
Full list: `PRODUCT.md` §8.
