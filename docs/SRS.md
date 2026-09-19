# Social Data Engine — Software Requirements Specification

Version 1.0 · 2026-08-22 · Traceability: each requirement cites its PRD row.
Every requirement has an **observable acceptance condition** — a check a
reviewer can run or read the result of. The **Proof** line states today's
status honestly: a passing test/command, or `GAP` (unimplemented).

Conventions: MUST/SHOULD per PRD. "Run summary" = `RunSummary` from
`src/runtime/engine.py`. "Manifest" = `data/manifests/*.jsonl` artifacts.

---

## 1. Actor (PRD §1)

**FR-ACT-001** — Every actor exposes `actor_id` (lowercase dot-namespaced
identifier), `actor_version` (non-empty, whitespace-free), `provider`.
*Acceptance:* constructing/declaring an actor missing or malformed on any field
raises a validation error naming the field; a valid actor's identity appears
verbatim in its run summary and checkpoint provenance.
*Proof:* `tests/test_actor_harness.py` (invalid-identity cases); `GAP` for
checkpoint field assertion → covered by FR-PROV-002.

**FR-ACT-002** — An actor may declare zero capabilities.
*Acceptance:* zero-capability actor passes harness validation, executes, and
its summary records `capabilities: []`.
*Proof:* `tests/test_actor_harness.py` (empty-caps fake actor).

**FR-ACT-003** — Duplicate capability declarations are rejected; non-`Capability`
entries are rejected.
*Acceptance:* declaring the same capability twice raises with the duplicated
name in the message.
*Proof:* `tests/test_actor_harness.py` (duplicate-caps case).

**FR-ACT-004** — Two versions of one actor produce distinguishable run metadata.
*Acceptance:* same `job_id` run under `actor_version` 1.0.0 vs 1.1.0 yields
different `actor_version` values in both summaries and checkpoint provenance.
*Proof:* `tests/test_actor_harness.py` (version-distinguishability case).

**FR-ACT-005** (SHOULD) — Optional actor metadata (description) flows into
provenance. *Acceptance:* present in summary when declared, absent otherwise.
*Proof:* `GAP`.

## 2. Harness (PRD §2)

**FR-HAR-001** — `RunInput` serializes to JSON-compatible dict and round-trips
equal; malformed inputs (missing keys, unknown keys, bad types, empty target,
reserved-key collisions) fail closed with explicit errors.
*Acceptance:* `RunInput.from_dict(RunInput(...).to_dict()) == original` for
valid inputs; each listed malformation raises `ValueError` naming the problem.
*Proof:* `tests/test_actor_harness.py` (serialization + malformed-input cases).

**FR-HAR-002** — Harness validates identity binding and declarations **before**
runtime invocation; invalid runs write no checkpoints.
*Acceptance:* identity mismatch or invalid declaration raises; state directory
contains no new files for the rejected run.
*Proof:* `tests/test_actor_harness.py` (mismatch case asserts pre-execution failure).

**FR-HAR-003** — Harness adds no execution wrapper: the returned summary is the
runtime's own `RunSummary` type, enriched only with actor provenance.
*Acceptance:* `type(result)` identical via harness and direct-runtime paths;
runtime source imports no provider modules.
*Proof:* `tests/test_actor_harness.py` (same-runtime execution case);
`src/runtime/engine.py` import audit (no provider imports).

**FR-HAR-004** (SHOULD) — Dry-run mode validates and reports without execution.
*Acceptance:* dry run returns a validation report, creates no state/data files.
*Proof:* `GAP`.

**FR-HAR-005** — Config precedence: explicit `RunOptions` wins; `RunInput.config`
axes must be `RunOptions` fields; supplying both is rejected (no silent merge).
*Acceptance:* both-sources run raises "ambiguous run configuration"; unknown
config axis raises naming supported axes.
*Proof:* `tests/test_actor_harness.py` (config-projection cases).

## 3. Acquisition (PRD §3)

**FR-ACQ-001** — URL forms `…/(video|photo)/<digits>` parse to the canonical id.
*Acceptance:* both forms extract identical id for the same content id; other
forms rejected.
*Proof:* `tests/test_tiktok_pagination.py` / collector regex tests.

**FR-ACQ-002** — Nested replies expand recursively (fixed round budget) and
every reply retains `parent_comment_id` linkage **after** dedup.
*Acceptance:* threaded fixture processed → all replies resolvable to a
top-level ancestor; dedup drops no parent required by a surviving child.
*Proof:* `tests/run_dedup_quality_tests.py` (threading-preservation cases);
`scripts/trace_comment.py --trace-all` (44/44 traced).

**FR-ACQ-003** — Interrupted runs resume by pinned `job_id` without duplicates.
*Acceptance:* kill mid-run, re-run same `job_id` → final unique count equals
full-run unique count; zero duplicate ids appended.
*Proof:* `tests/test_acquisition_runtime.py` (resume case);
`tests/test_checkpoint_fail_closed.py` (corrupt checkpoint → explicit
`checkpoint_corrupt` terminal failure, never silent fresh-run).

**FR-ACQ-004** — Coverage = captured/reported computed and present in summary.
*Acceptance:* synthetic fixture with known reported count yields exact ratio.
*Proof:* `tests/test_acquisition_runtime.py` metrics assertions.

**FR-ACQ-005** (SHOULD) — Wall-clock and network-call budgets enforced.
*Acceptance:* run exceeding configured wall-clock/network axis terminates with
a classified reason. *Proof:* `GAP` (axes exist in `ExecutionBudget`; not consumed).

## 4. Processing (PRD §4)

**FR-PRC-001** — `text_raw` coexists with `text_normalized` on every curated record.
*Acceptance:* curated JSONL rows contain both keys; normalization never overwrites raw.
*Proof:* `tests/test_pipeline.py`.

**FR-PRC-002** — Dedup preserves threading and records `dup_rate`.
*Acceptance:* duplicate child removal never orphans a kept child; metrics expose dup_rate.
*Proof:* `tests/test_dedup.py`, `tests/run_dedup_quality_tests.py`.

**FR-PRC-003** — Quality gate (default 0.35) filters curated output; gate
adjustment is an explicit, logged action (`ADJUST_GATE`).
*Acceptance:* records below threshold absent from curated tier; adjust action
appears in improve manifest when triggered.
*Proof:* `tests/run_dedup_quality_tests.py`; `tests/test_self_improvement.py`.

## 5. Dataset (PRD §5)

**FR-DAT-001** — Append-only tiered JSONL under `data/{raw,curated,normalized,enriched}/`.
*Acceptance:* writes append; no tier ever rewritten wholesale by the pipeline.
*Proof:* runtime dataset writer tests; storage-layout constitution §12.

**FR-DAT-002** — Legacy checkpoint/dataset field names evolve additively only.
*Acceptance:* checkpoint written by pre-change code resumes post-change;
`status` legacy values untouched alongside new `lifecycle` key.
*Proof:* `tests/test_checkpoint_fail_closed.py`; PR #5 additive-only diff review.

**FR-DAT-003** — Dataset location discoverable from summary.
*Acceptance:* summary exposes dataset path/prefix used by the run.
*Proof:* `GAP` (paths derivable from RunContext but not summarized — scheduled Phase 2).

## 6. Run (PRD §6)

**FR-RUN-001** — Lifecycle states CREATED/STARTED/COMPLETED/FAILED/TERMINATED
derive deterministically from the existing `Outcome` taxonomy; no new terminal
meanings invented ad hoc.
*Acceptance:* mapping function is pure; same outcome → same lifecycle state
across calls/versions.
*Proof:* `tests/test_actor_harness.py` (lifecycle mapping cases).

**FR-RUN-002** — Budgets terminate finitely: unique-item hard cap (incl.
mid-page truncation), page cap, retry budget.
*Acceptance:* fixture exceeding caps stops at cap with classified reason.
*Proof:* `tests/test_acquisition_runtime.py`, `tests/test_acquisition_hardening.py`.

**FR-RUN-003** — Failed runs still emit a summary and preserve partial artifacts
already durably written (dataset-before-checkpoint ordering).
*Acceptance:* forced-failure run returns FAILED summary; previously written
records remain readable and resume picks them up.
*Proof:* `tests/test_acquisition_runtime.py` (failure/resume cases).

**FR-RUN-004** — Resume requires explicit job identity; unpinned runs derive
fresh identity (never silently attach to another run's state).
*Acceptance:* two unpinned runs produce distinct job ids; pinned rerun attaches.
*Proof:* `tests/test_actor_harness.py` (pinned job_id resume case).

## 7. Loop (PRD §7)

**FR-LOOP-001** — Internal loops terminate within `max_iter` (default 3).
*Acceptance:* unstable metrics + exhausted budget → return best-effort result,
no infinite loop (test with always-failing processor).
*Proof:* `tests/test_self_improvement.py`.

**FR-LOOP-002** — Each iteration appends actions + rationale + resulting
metrics to `data/manifests/<video_id>.improve.jsonl`.
*Acceptance:* N-iteration run → N manifest lines, ordered, timestamped.
*Proof:* `tests/test_self_improvement.py` (manifest assertions).

**FR-LOOP-003** — No loop mutates declared intent (target URL, actor identity,
acceptance criteria). Autonomous threshold adjustment may only **tighten**
quality gates; loosening requires external configuration (SDD invariant 3).
*Acceptance:* loop parameter-override surface (`_apply`) contains only
collection-parameter axes; code review + test that target/identity fields are
absent from override keys.
*Proof:* `src/pipeline/improve.py::_apply` axis audit — `GAP` as an explicit
test; scheduled Phase 4.

## 8. Self-improvement (PRD §8)

**FR-SI-001** — Plans/lessons recorded with provenance (append-only, timestamped).
*Acceptance:* manifest line contains iteration, actions, rationale, metrics, timestamp.
*Proof:* `tests/test_self_improvement.py`.

**FR-SI-002** — Planner deterministic: same metrics → same plan, always.
*Acceptance:* repeated `plan()` calls with identical input produce identical
`ImprovementPlan`; no network/LLM imports in planner module.
*Proof:* `tests/test_self_improvement.py` (determinism case).

**FR-SI-003** — Improvement claims require measured before/after delta
(`MIN_IMPROVEMENT_DELTA = 0.05` on coverage for loop continuation; lesson-level
delta reporting for cross-run claims).
*Acceptance:* `is_improving(prev, cur)` false when delta < 0.05; any doc claim
of improvement cites a manifest delta line.
*Proof:* loop-continuation logic proven in `tests/test_self_improvement.py`;
lesson-level delta report `GAP` (Phase 4).

**FR-SI-004** (SHOULD) — Lesson schema v1 with accepted/rejected flag.
*Acceptance:* lessons persist with outcome verdicts; rejected lessons excluded
from future strategy inputs. *Proof:* `GAP`.

## 9. Provenance (PRD §9)

**FR-PROV-001** — Every curated row traceable to a raw id.
*Acceptance:* `raw.id == curated.id` across a full synthetic pipeline run.
*Proof:* `tests/test_pipeline.py`; `scripts/trace_comment.py --trace-all` (44/44).

**FR-PROV-002** — Run manifests/summaries record actor id/version, declared
capabilities, budgets, outcome, lifecycle, `policy_model_version`.
*Acceptance:* fields present and correct for harness-run actors.
*Proof:* `tests/test_actor_harness.py` (provenance assertions).

**FR-PROV-003** — Manifests append-only; verification function/command
replays raw→curated linkage for a video id.
*Acceptance:* verification exits 0 on consistent data, non-zero naming the
broken link otherwise.
*Proof:* `scripts/trace_comment.py` (manual); `GAP` as required command (Phase 2).

**FR-PROV-004** (SHOULD) — Manifest schema version stamped; readers reject
unknown major versions. *Proof:* `GAP`.

## 10. Preferences (PRD §10)

**FR-PREF-001** — Preferences enter only as explicit `RunInput.config`/CLI
flags; no silent inference anywhere.
*Acceptance:* grep-level guarantee — no code path reads preferences from
sources other than explicit inputs; config validation rejects non-`RunOptions` axes.
*Proof:* `tests/test_actor_harness.py` config cases; audit `GAP` as explicit
check (Phase 2).

## 11. Policy (PRD §11)

**FR-POL-001** — `src/policy/models.py` is the single vocabulary source;
declarations use `Capability` instances.
*Acceptance:* harness rejects non-Capability declarations (already enforced).
*Proof:* FR-ACT-003 proof.

**FR-POL-002** — Evaluator v0: deterministic `(actor, capability, context) →
ALLOW/DENY/REQUIRE_APPROVAL`, deny-by-default under any uncertainty; no
network/model calls.
*Acceptance:* table-driven tests: known-allow, known-deny, unknown-capability→DENY,
evaluator-exception→DENY; identical inputs → identical decisions.
*Proof:* `GAP` — Phase 2, first roadmap item.

**FR-POL-003** — Denials surface as `policy_denied` termination reason (added
additively) with reason code in summary.
*Acceptance:* gated-off run yields FAILED/TERMINATED summary containing reason
`policy_denied`; legacy reasons unchanged.
*Proof:* `GAP` — Phase 2.

**FR-POL-004** — Sanctioned entry points cannot bypass policy: the harness
(and, after Phase 3 unification, the CLI) routes every externally-submitted
actor through the evaluator gate. Direct `AcquisitionRuntime` use remains a
trusted-internal path at the FR-SEC-001 trust level — a library cannot
enforce beyond its caller; this is a documented limitation, not an allowance.
*Acceptance:* a harness run whose capability the evaluator denies terminates
with reason `policy_denied` and writes no checkpoint; `docs/RUNTIME.md` and
`SDD.md` state the trusted-internal status of the direct path; NFR-007 import
audit passes.
*Proof:* `GAP` — Phase 2.

**FR-POL-005** (SHOULD) — Versioned named profiles. *Proof:* `GAP`.

## 12. Safety (PRD §12)

**FR-SAF-001** — Live-test-before-merge gate for collection-behavior changes.
*Acceptance:* `agents.md` checklist followed on collection-touching PRs;
non-collection PRs record honest not-run justification (PR #5 precedent).
*Proof:* process gate — evidenced in PR #5 report.

**FR-SAF-002** — Human-in-the-loop login/captcha; automation never takes over
authentication.
*Acceptance:* login flows open visible browser and wait; no stored-credential
fallback exists.
*Proof:* `run.sh` flow + `agents.md` procedure; grep gate (FR-SEC-002).

**FR-SAF-003** — Destructive ops require explicit flags (e.g. version bump
dry-run default; corrupt-checkpoint recovery is a human decision).
*Acceptance:* `scripts/version_bump.py` writes nothing without `--commit --push`.
*Proof:* script default; `tests/test_checkpoint_fail_closed.py`.

## 13. Security (PRD §13)

**FR-SEC-001** — Actors run in-process with host trust — documented limitation
surfaced in all actor documentation until sandboxing is explicitly decided.
*Acceptance:* `docs/RUNTIME.md` + `SDD.md` state it; no doc claims isolation.
*Proof:* current docs.

**FR-SEC-002** — Zero plaintext credentials; grep gate over `src/`.
*Acceptance:* `grep -rn "LINKEDIN_PASSWORD\|LINKEDIN_USERNAME" src/` → 0 hits.
*Proof:* CI/pre-merge checklist.

**FR-SEC-003** — Contract models are non-secret by contract and fail closed on
malformed context.
*Acceptance:* `CapabilityRequest.metadata`/`RunInput.payload` documented
non-secret; malformed inputs rejected (FR-HAR-001).
*Proof:* model docstrings + tests.

**FR-SEC-004** (SHOULD) — Mechanical secret scanner over contract payloads.
*Acceptance:* scanner test with planted secret-like values fails loudly.
*Proof:* `GAP`.

## 14. Transparency (PRD §14)

**FR-TRANS-001/002** — Every run emits summary with counts, coverage, quality
mean, durations, termination reason, lifecycle.
*Acceptance:* present on success AND failure paths.
*Proof:* `tests/test_acquisition_runtime.py`, `tests/test_actor_harness.py`.

**FR-TRANS-003** (SHOULD) — BrowserAgent traces persisted as structured logs.
*Proof:* `GAP` (trace exists in-memory/JSON dump, not integrated with manifests).

**FR-TRANS-004** (SHOULD) — Run inspection command over checkpoints+manifests.
*Proof:* `GAP`.

## 15. External agent integration (PRD §15)

**FR-INT-001** — Import and CLI parity for run/resume/inspect.
*Acceptance:* same job via `python -u src/tiktok_linkedin.py …` and programmatic
harness call yields equivalent summaries. *Proof:* `GAP` (CLI predates harness —
Phase 3/6 wiring).

**FR-INT-002** — `RunInput`/`RunSummary` carry schema/version constants;
unknown major versions rejected.
*Acceptance:* version field present in serialized forms; mismatch raises.
*Proof:* partial (policy models stamped); RunInput/RunSummary `GAP` — Phase 2.

**FR-INT-003** — Stable exit-code taxonomy for CLI. *Proof:* `GAP` — Phase 6.

**FR-INT-004** (SHOULD) — Framework-neutral agent-loop example. *Proof:* `GAP`.

---

## Non-functional requirements

**NFR-001 Deterministic planning** — no LLM/network in planning loops.
*Acceptance:* planner modules import neither; tests run offline. *Proof:* existing suites run offline.

**NFR-002 Stdlib-first core** — new core deps require a decision record.
*Acceptance:* `requirements.txt` diff in any PR cites `DECISIONS.md`. *Proof:* process gate.

**NFR-003 Finite execution** — every entry point bounds at least one axis; fully-unbounded budgets unrepresentable.
*Acceptance:* `ExecutionBudget` all-`None` raises. *Proof:* `tests/test_policy_models.py`. Wall-clock/network axes: `GAP` (FR-ACQ-005).

**NFR-004 Auditability** — significant decisions leave inspectable traces (checkpoints, manifests, summaries).
*Acceptance:* any completed run reconstructable from disk artifacts alone. *Proof:* partial (FR-PROV-003 command `GAP`).

**NFR-005 Portability** — deterministic suites pass on CPython ≥3.10 without display/network.
*Acceptance:* full suite green in headless CI. *Proof:* all suites pass in this environment.

**NFR-006 Safe defaults** — unset configuration resolves deny/dry-run/visible/capped.
*Acceptance:* version bump dry-runs; runs capped by defaults. *Proof:* partial (Constitution §2 gaps listed).

**NFR-007 Provider-blind runtime** — `src/runtime/` imports no provider modules.
*Acceptance:* import audit test/gate. *Proof:* structural today; explicit test `GAP` (add in Phase 2 PR).

**NFR-008 Offline operability** — full deterministic suite requires no network.
*Acceptance:* suite green with networking disabled. *Proof:* current environment.

**NFR-009 Byte-compat evolution** — legacy checkpoint/dataset fields never renamed.
*Acceptance:* old-format checkpoint resumes (FR-DAT-002). *Proof:* existing tests.

**NFR-010 Honest labeling** — docs' status labels move only with proof.
*Acceptance:* Constitution amendment rule followed; labels cite tests. *Proof:* process gate (PR #5 precedent).
