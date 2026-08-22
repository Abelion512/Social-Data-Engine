# Social Data Engine — Current State

Version 1.0 · 2026-08-22 · Evidence-based map of what exists. Status labels
follow the Constitution's discipline: upgraded only with tests/code that prove
the stronger claim. Branch note: Harness/Actor contract work is **PR #5 (open,
unmerged)** on `feat/actor-harness-contract`. Per the honest-labeling rule,
PR #5 scope is labeled **PENDING / EXPERIMENTAL** in §1b — nothing from an
unmerged PR counts as IMPLEMENTED here, no matter how green its tests are.

---

## 1. IMPLEMENTED (enforced by code + deterministic tests)

| Capability | Evidence |
|---|---|
| TikTok acquisition: photo+video URLs, threaded reply expansion, media/sticker capture | `src/collector.py`, `src/providers/tiktok.py`; live run #2 (44 curated, quality 0.992 mean — **coverage 22 % guest rate-limit; the ≥95 % stabilize target is NOT yet met and awaits a logged-in desktop run**, Phase 3 gate); pagination + hardening suites |
| Provider-independent runtime: budgets (items/pages/retries), checkpoint/resume, termination taxonomy, metrics | `src/runtime/engine.py`, `checkpoint.py`, `termination.py`; `tests/test_acquisition_runtime.py` (15), hardening (11) |
| Fail-closed checkpoint semantics; explicit corrupt-checkpoint terminal failure | `tests/test_checkpoint_fail_closed.py` (4); Constitution §3/§13 |
| Processing pipeline: normalize (raw+normalized coexist), dedup w/ thread preservation, quality gate 0.35 | `src/pipeline/{stages,dedup,quality}.py`; dedup/quality suites (8+4); pipeline suite (13) |
| Deterministic self-improvement planner + bounded loop (`max_iter=3`) with per-iteration manifests | `src/pipeline/improve.py`; `tests/test_self_improvement.py`; coverage-delta continuation logic |
| Policy models: capability vocabulary, capability request, execution budget (must bound ≥1 axis), decision enum, identifier validation | `src/policy/models.py`; `tests/test_policy_models.py` (8) |
| Harness/Actor contract: `RunInput` (fail-closed, serializable, identity binding), `ActorHarness` (structural capability validation, zero-caps valid, dupes rejected, config projection to RunOptions axes), lifecycle states derived from outcomes, actor provenance in summaries/checkpoints (additive) | `src/runtime/harness.py`, `actor.py`; `tests/test_actor_harness.py` (18) — PR #5 |
| TikTok-shaped actor executing through unmodified runtime incl. resume past item boundary | `src/providers/tiktok_actor.py`; PR #5 test proving same-runtime execution for fake + TikTok-shaped actors |
| Credential hygiene: cookie/profile auth only, no plaintext creds | grep gate in pre-merge checklist |

## 1b. PENDING MERGE — PR #5 (open): EXPERIMENTAL until merged

Everything below exists ONLY on the open PR branch. It is not IMPLEMENTED by
this document's standard (merged + proven) and must not be cited as such.

| Capability | Evidence | Status |
|---|---|---|
| Harness/Actor contract: `RunInput` (fail-closed, serializable, identity binding), `ActorHarness` (structural capability validation, zero-caps valid, dupes rejected, config projection to RunOptions axes), lifecycle states derived from outcomes, actor provenance in summaries/checkpoints (additive) | `src/runtime/harness.py`, `src/runtime/actor.py`; `tests/test_actor_harness.py` (22) on the PR branch | Pending merge |
| Fake + TikTok-shaped actors executing through ONE unmodified runtime; resume past item boundary without duplication | PR #5 same-runtime-equivalence tests | Pending merge |
| Real production page source wired into the actor via DI: `CollectorApiPageSource` → unmodified `fetch_comments_api`; collector challenge classifications (`auth_blocked`/`fetch_failure`) pass through instead of being laundered into empty pages; reply-thread expansion deliberately stays in `_capture_pass` | Hardening-cycle tests: wire-parameter assertions, async-source resume through shared runtime, auth-blocked termination | **Wired, deterministic-tested; live desktop proof still pending** (visible-browser human-in-the-loop run) |

## 2. PARTIALLY IMPLEMENTED (works in some paths/scopes; gaps named)

| Capability | What exists | Gap |
|---|---|---|
| Provenance | Curated↔raw id traceability proven (44/44 via `scripts/trace_comment.py`); improve manifests per iteration; run summaries carry outcome/reason (+ lifecycle/actor fields land with PR #5) | Manifest writing not universal at dataset append; no schema version stamp; verification command is a script habit, not a required interface (FR-PROV-003/004, FR-DAT-003) |
| Self-improvement measurement | Loop-continuation requires ≥5pp coverage delta; metrics before/after recorded per iteration | No lesson-level delta reports or accept/reject schema; "improved" claims not yet machine-checkable across runs (FR-SI-003 completion, FR-SI-004) |
| Bounded execution | Item/page/retry budgets enforced; fully-unbounded budgets unrepresentable | Wall-clock and network-call axes exist in `ExecutionBudget` but are consumed nowhere (FR-ACQ-005); legacy scripts/harness agents not uniformly bounded (Constitution §4) |
| Transparency | Summaries on success+failure; agent trace logs exist in-memory/JSON dump | Traces not integrated with manifest evidence chain (FR-TRANS-003); no run-inspection command (FR-TRANS-004) |
| Safe defaults | Version bump dry-run default; capped collection defaults | Arbitrary scripts can open network/write anywhere (Constitution §2 gap) |
| External-agent surface | Serialized `RunInput`/`RunSummary`; import path complete | CLI predates harness contract (two entry truths, FR-INT-001); schemas lack version stamps on RunInput/RunSummary (FR-INT-002); no packaging (D-012) |

## 3. EXPERIMENTAL (exists, not product-grade, reclassified by D-011)

| Component | Reality | Product disposition |
|---|---|---|
| `src/harness/` BrowserAgent + tools + registry | Goal-driven browser tool loop, dry-mode trace, manus-style toolkit selection | Acquisition tooling that must become a page-source implementation behind the TikTok actor (Phase 3); naming collision documented |
| Stealth/captcha helpers (`src/harness/human.py`) | Fingerprint stealth, humanized delays, captcha pre-resolution | Liability per D-007: never a feature; gated behind future REQUIRE_APPROVAL; restricts supported deployment modes today |
| LinkedIn provider stub | Pluggable hook, no real collection | Proof-of-concept for provider registry shape only |
| CSV export / export manifests | Utility scripts under `src/export/` | Convenience tier, not dataset-management commitment |
| Env-gated headless Camoufox fallback | Short runs (scrolls ≤50) on servers | Documented constraint; real live-test gate needs desktop human-in-loop |

## 4. NOT IMPLEMENTED

| Capability | Spec reference |
|---|---|
| Policy evaluator / enforcement of declared capabilities (deny-by-default) | FR-POL-002..004 — **Phase 2, next** |
| `policy_denied` termination reason surfaced through summaries | FR-POL-003 |
| Manifest schema v1 + version stamps + verification-as-interface | FR-PROV-003/004 |
| Runtime Human Override verbs (stop/approve/deny/escalate over executing actions) | Constitution §10 PLANNED; LATER roadmap |
| Lesson store with accepted/rejected verdicts; cross-run strategy learning | FR-SI-004; LATER |
| Preferences store | LATER |
| Second real provider end-to-end | Phase 5 |
| Sandboxing / process isolation of actors | OUT by decision (documented limitation, FR-SEC-001) |
| Scheduler / autonomous loop engine / dashboard / cloud | OUT (PRODUCT.md §8) |
| Packaging (`pyproject.toml`) / published schemas / stable exit codes | Phase 6 |

## 5. Test inventory (deterministic gates, all green as of this session)

`test_policy_models` (8) · `test_acquisition_runtime` (15) ·
`test_checkpoint_fail_closed` (4) · `test_tiktok_pagination` (11) ·
`test_acquisition_hardening` (11) · dedup/quality (8+4) ·
`test_pipeline` (13) · `test_self_improvement` · `test_actor_harness` (22, on PR #5 branch) ·
`py_compile` over `src/**` + `tests/**`.

Live-test gate: last full desktop live run = run #2 (see Constitution header /
`docs/VERIFICATION.md`); PR #5 honestly recorded it as not-run (headless env,
no collection-behavior change). This remains the standing merge-gate procedure.

## 6. Known debts & risks

1. **Capability–policy gap** — declarations are advisory; top security risk as autonomy grows (PRODUCT.md §11). Resolution owned by Phase 2.
2. **Dual harness naming** — docs fixed (D-011); physical rename deferred to Phase 3.
3. **Two CLI truths** — legacy collector CLI vs harness path; unify Phase 3.
4. **No lockfile** — lower-bound pins only (Constitution §9); acceptable until packaging phase.
5. **PR #5 unmerged** — CURRENT-STATE and spec assume its contract; if review changes it, SRS FR-ACT/HAR rows update accordingly.
