# 2026-09-19 — `ponytail-audit` pass (whole repo)

**Scope:** over-engineering and complexity only (the audit boundary in
`skills/ponytail-audit`). Correctness/security/perf findings stay in their own
passes — `docs/VERIFICATION.md` §11–§13.

**Method (reproducible, stdlib only):** AST scan of every `.py` for top-level
definitions with **zero references anywhere** (own file, other modules, tests,
docs); reverse import/attribute scan for modules and symbols that no non-test
file calls; a repo-wide unused-import scan; then grep-verification of each hit
before anything was deleted.

---

## 1. Applied

### `delete:` ten definitions, zero callers anywhere

| # | Symbol | Why it went |
|---|---|---|
| 1 | `scripts/suggest_bump.py` `MAJOR_TYPES`, `OTHER_TYPES` | empty/never-read sets; the docstring already states breaking is signalled by `!`/footer, not a type |
| 2 | `src/browser_selector.py` `_detect_browsers_async` | async twin of `detect_browsers()` with no caller |
| 3 | `src/browser_selector.py` `_TIKTOK_SESSION_COOKIE_NAMES` | never read **and misleading**: `_read_tiktok_session` deliberately tests only `sessionid` (ttwid/uid always exist → false "logged in"). The explanation lives at the real check site |
| 4 | `src/collector.py` `CLICK_COMMENT_PANEL_JS` | superseded panel-opening JS |
| 5 | `src/export_tiktok_cookies.py` `serialize_cookie` | byte-identical copy of `scripts/export_tiktok_cookies_nodriver.py::ser` (the live one) — deleting the dead copy removed the duplication as well |
| 6 | `src/pipeline/legacy.py` `TOXICITY_MAX`, `hash_simhash` | unread threshold; hash wrapper nothing called |
| 7 | `src/providers/linkedin.py` `_POST_ID_RE` | compiled, never used |
| 8 | `src/pipeline/identity.py` `cross_platform_match` + `_similarity` | 0 callers, 0 tests — yet labelled `✅` in `VERIFICATION.md` ×4 and `VERSIONING.md` §11. Labelling rule (`docs/ENGINEERING_CONSTITUTION.md`) says a label needs proof: docs corrected instead of keeping the claim |

### `delete:` dead imports (repo-wide scan now 0)

`collector.py` (`AcquisitionMetrics`, `write_jsonl`), `tiktok_actor.py`
(`Capability`), `tiktok_schema.py` (`json`, `datetime`, `timezone`, `Union`,
`Dict`, `Any` — leftovers from the runtime move), `suggest_bump.py` (`sys`).

### `yagni:` hand-maintained re-export table → PEP 562 fallback

`src/pipeline/__init__.py` carried `_LEGACY_PUBLIC` + 22 explicit assignments,
i.e. every legacy symbol had to be listed twice (add one to `legacy.py` and the
package silently stops exposing it). Replaced by one `__getattr__` delegation to
`src.pipeline.legacy`. **Verified after**: all 22 previously-exported names, the
private ones (`_ROOT`, `_discover_videos`), `from src.pipeline import dedup,
quality, identity, stages, improve, legacy`, `pipeline.STAGES["dedup"] is
legacy.stage_dedup`, an unknown attribute still raising `AttributeError`, and
`python -m src.pipeline.legacy --help`.

### `stdlib:` popcount

`legacy.hamming()` → `(a ^ b).bit_count()` (3.10+; CI is 3.12, local 3.10.12).
Equivalence checked against `bin(x).count("1")` on 200 000 random 64-bit pairs:
0 mismatches.

### `native:`/gate: the CI ponytail step could not fail

`.github/workflows/ci.yml` step 6 ended in an unconditional
`echo "✅ ponytail ladder review logged"` — decoration. It now greps
`TODO|FIXME|XXX|HACK` in `src/` + `scripts/` and fails the build (currently 0
hits, §11 removed the last ones). The `src.pipeline.identity` symbol check was
updated for the deletion in §1.8.

### `ponytail:` markers added (ceilings that were silent)

* `legacy.stage_dedup` — near-dup check compares only inside a 16-bit simhash
  bucket (recall ceiling), upgrade path named.
* `collector._probe_reported_count` — the coverage denominator is the
  DOM-rendered count, so it is a soft number, not ground truth.

**net for this pass: −110 lines of `src/` + `scripts/`, −0 deps.**

## 2. Deferred (ranked, with triggers — ledger in `docs/PONYTAIL.md` §6)

1. **`yagni:` two pipelines.** `src/pipeline/{stages,dedup,quality,identity,thread_builder}.py`
   (616 lines) re-implement normalize/dedup/quality — the stages the CLI really
   runs live inline in `legacy.py`; no non-test file calls them. `improve.py`
   (489) has no caller either. Wiring them changes what lands in `curated/`
   (different dedup algorithm + serialization) and is therefore **live-gated**;
   deleting them would delete spec'd, tested capability. Deferred with the
   Phase 3 "two CLI truths" trigger.
2. **`yagni:` unreachable-from-CLI architecture.** `runtime/ harness/ policy/
   providers/ schema/` (~2 600 lines) plus their suites. Requested by `docs/SRS.md` /
   `docs/PRD.md`, labelled `IMPLEMENTED`/`PENDING`/`EXPERIMENTAL` in `docs/CURRENT-STATE.md`
   §1/§1b/§3. Kept — see `docs/PONYTAIL.md` §5 boundary.
3. **`yagni:` `legacy.llm_enrich_identities`** is on the live path but its LLM
   leg is skipped without a 9Router key; not cut (it is the enrich stage).
4. **`stdlib:` `config.env_load`** — a 12-line `.env` reader; stdlib has no dotenv
   equivalent, third-party is banned by `agents.md` §4, so it stays.

## 3. Left alone on purpose

Validation at trust boundaries, the S-G2/TM-26 path helpers, error handling,
provenance, and anything requested by `docs/SRS.md`/`docs/PRD.md`. Nothing under
`src/runtime/`, `src/harness/`, `src/policy/`, `src/providers/`, `src/schema/`
was touched.

## 4. Evidence

| Gate | Result |
|---|---|
| `python -m py_compile src/*.py src/*/*.py scripts/*.py tests/*.py` | OK |
| Deterministic suites (`tests/test_*.py` + `tests/run_*_tests.py`) | **19/19 green** |
| CI import + symbol cross-check (updated for §1.8) | OK |
| Repo-wide unused-import scan | 0 |
| Dead top-level symbol scan (own file + repo + docs) | 0 remaining beyond §2 |
| `hamming` equivalence, 200 000 random pairs | 0 mismatches |
| `python -m src.pipeline.legacy --help` / `scripts/suggest_bump.py` | OK |

**Live gate: not run** — headless sandbox (no display/CDP/Camoufox), same
constraint as §7/§9/§11/§12/§13. This pass changes no collection behaviour:
deletions of unreferenced code, one popcount substitution (proven equivalent),
one re-export mechanism, docstrings. Any real acquisition change still owes the
`agents.md` live test.
