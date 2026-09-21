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
| Processing pipeline: normalize (raw+normalized coexist), dedup w/ thread preservation, quality gate 0.35 | `src/pipeline/{stages,dedup,quality}.py`; dedup/quality suites (8+4); pipeline suite (13). Near-dup tier prunes on a provably-safe size window (5 000 records: 13 s → 0.4 s, identical kept set — `tests/test_dedup_scaling.py`, VERIFICATION §12). **Which code runs:** the CLI executes `src/pipeline/legacy.py`'s inline stages; the modular modules above are the tested refactor layer, not wired yet (debt §6.8, `docs/PONYTAIL.md` §6) |
| Deterministic self-improvement planner + bounded loop (`max_iter=3`) with per-iteration manifests | `src/pipeline/improve.py`; `tests/test_self_improvement.py`; coverage-delta continuation logic |
| Policy models: capability vocabulary, capability request, execution budget (must bound ≥1 axis), decision enum, identifier validation | `src/policy/models.py`; `tests/test_policy_models.py` (8) |
| Harness/Actor contract: `RunInput` (fail-closed, serializable, identity binding), `ActorHarness` (structural capability validation, zero-caps valid, dupes rejected, config projection to RunOptions axes), lifecycle states derived from outcomes, actor provenance in summaries/checkpoints (additive) | `src/runtime/harness.py`, `actor.py`; `tests/test_actor_harness.py` (18) — PR #5 |
| TikTok-shaped actor executing through unmodified runtime incl. resume past item boundary | `src/providers/tiktok_actor.py`; PR #5 test proving same-runtime execution for fake + TikTok-shaped actors |
| Credential hygiene: cookie/profile auth only, no plaintext creds | grep gate in pre-merge checklist; cookie exports + PII reports/state written 0600 (`src/runtime/context.py::write_private_text`) |
| Input validation at every non-runtime entry point: ids validated against the slug charset and rooted-resolved before any path is built (exporters, legacy stage CLI, LinkedIn consumer, manifest writer); LinkedIn handles charset-checked before they reach `linkedin-cli` argv; declared 20/day connect budget + pacing enforced | `tests/test_input_validation.py` (15) — refusal per entry point incl. a "no file written" assertion; threat model TM-26/27/28 |
| One canonical TikTok content-id parser (`src/tiktok_schema.parse_content_id`) — four former copies collapsed; provider actor delegates | `tests/test_input_validation.py::test_content_id_parser_is_single_source` |
| Host-agnostic plugin surface: the same four tools (`sde_list_providers`, `sde_probe`, `sde_collect`, `sde_run_status`) reachable three ways — **stdio MCP**, **Streamable-HTTP MCP** (`src/mcp_http.py`; loopback by default, hardened 2026-09-21 per TM-29/30), and a **generic plugin folder** (`integrations/plugin/`: manifest + JS adapter + installer, `node:` builtins only, no shell, no npm deps) | `tests/test_plugin_host.py` (17), `tests/test_mcp_http_security.py` (22), `tests/test_run_status.py` (12), `tests/test_import_layering.py` (9); mapping/limits in `docs/INTEGRATIONS/PLUGIN.md` — **no host has been executed against this surface, so no compatibility with any specific agent is claimed or proven** |

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
| Provenance | Curated↔raw id traceability proven (44/44 via `scripts/trace_comment.py`); improve manifests per iteration; run summaries carry outcome/reason (+ lifecycle/actor fields land with PR #5); **`manifest.v1` schema stamp (2026-09-21)** | Manifest writing still not universal at dataset append; the stamp exists but only `build_manifest`/`write_manifest` set it; verification is still a script habit rather than a required interface (FR-PROV-003/004, FR-DAT-003) |
| Self-improvement measurement | Loop-continuation requires ≥5pp coverage delta; metrics before/after recorded per iteration | No lesson-level delta reports or accept/reject schema; "improved" claims not yet machine-checkable across runs (FR-SI-003 completion, FR-SI-004) |
| Bounded execution | Item/page/retry budgets enforced; fully-unbounded budgets unrepresentable | Wall-clock and network-call axes exist in `ExecutionBudget` but are consumed nowhere (FR-ACQ-005); legacy scripts/harness agents not uniformly bounded (Constitution §4) |
| Transparency | Summaries on success+failure; agent trace logs exist in-memory/JSON dump; **`sde_run_status` (2026-09-21) reads checkpoint/loop/manifest/improve artifacts and reports coverage, termination reason and iteration count without hand-reading files** | Read-only surface only — no queryable event stream, and traces are still not integrated with the manifest evidence chain (FR-TRANS-003, FR-TRANS-004 partially) |
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
| Verification-as-interface (stable exit codes, packaged schemas) + manifest writing at every dataset append | FR-PROV-003/004, FR-DAT-003 — the `manifest.v1` stamp landed 2026-09-21, universality did not |
| Runtime Human Override verbs (stop/approve/deny/escalate over executing actions) | Constitution §10 PLANNED; LATER roadmap |
| Lesson store with accepted/rejected verdicts; cross-run strategy learning | FR-SI-004; LATER |
| Preferences store | LATER |
| Second real provider end-to-end | Phase 5 |
| Sandboxing / process isolation of actors | OUT by decision (documented limitation, FR-SEC-001) |
| Scheduler / autonomous loop engine / dashboard / cloud | OUT (PRODUCT.md §8) |
| Packaging (`pyproject.toml`) / published schemas / stable exit codes | Phase 6 |

## 5. Test inventory (deterministic gates, all green as of this session)

`test_acquisition_hardening` (11) ·
`test_acquisition_runtime` (15) · `test_actor_harness` (22) ·
`test_canonical_roundtrip` (8) ·
`test_checkpoint_fail_closed` (4) · `test_dedup` (4) · `test_dedup_scaling` (6) ·
`test_import_layering` (9) · `test_input_validation` (15) · `test_loop_state` (13) ·
`test_mcp_http_security` (22) · `test_mcp_server` (14) · `test_pipeline` (13) ·
`test_plugin_host` (17) · `test_plugins` (7) ·
`test_policy_evaluator` (18) · `test_policy_models` (8) ·
`test_provider_asset_hygiene` (5) · `test_run_status` (12) ·
`test_security_gates_sg2` (17) · `test_self_improvement` (11) ·
`test_stages_io` (6) · `test_thread_builder` (7) · `test_tiktok_pagination` (11) ·
dedup/quality (8) ·
`py_compile` over `src/**` + `tests/**` and `bash -n`/`zsh -n run.sh`.
(25 suites / 283 assertions as of the 2026-09-21 plugin/hardening/perf pass — §15 of
`docs/VERIFICATION.md`; the 19/202 baseline is §14. Ladder + debt ledger:
`docs/PONYTAIL.md`.)

CI glob-discovers every `tests/test_*.py` + `tests/run_*_tests.py`, so a suite
is gated the moment it lands (previously only 2 of 14 ran). Deterministic only:
the live-test gate status below is unchanged by this inventory.

Live-test gate: last full desktop live run = run #2 (see Constitution header /
`docs/VERIFICATION.md`); PR #5 honestly recorded it as not-run (headless env,
no collection-behavior change). This remains the standing merge-gate procedure.

## 6. Known debts & risks

1. **Capability–policy gap** — declarations are advisory; top security risk as autonomy grows (PRODUCT.md §11). Resolution owned by Phase 2.
2. **Dual harness naming** — docs fixed (D-011); physical rename deferred to Phase 3.
3. **Two CLI truths** — legacy collector CLI vs harness path; unify Phase 3.
4. **No lockfile** — lower-bound pins only (Constitution §9); acceptable until packaging phase.
5. **PR #5 unmerged** — CURRENT-STATE and spec assume its contract; if review changes it, SRS FR-ACT/HAR rows update accordingly.
6. **Content-id parser is path-based (host-agnostic)** — deliberately unchanged from the four copies it replaced; host/route allow-listing is S-G7 (egress scoping, Phase 3→4) and needs the live gate because it changes navigation behaviour (threat model §2.14 residual).
7. **`StageRunner` stage names are caller-supplied** — internal-only callers today; root them when the runner is wired into the CLI (Phase 3).
8. **Two pipeline implementations** — `pipeline/legacy.py` inline stages (what the CLI runs) vs the modular `stages/dedup/quality/identity/thread_builder` (test-only, same stages; ~616 lines + `improve.py` 489 with no non-test caller). Unification is Phase 3/§6.3 work: it changes `curated/` output (different dedup algorithm + serialization), so it is live-gated, not a drive-by edit. Found + recorded by the 2026-09-19 ponytail audit (`docs/VERIFICATION.md` §14, ledger `docs/PONYTAIL.md` §6).
