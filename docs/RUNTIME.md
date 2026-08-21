# Acquisition Runtime — Architecture Decision

Status: implemented (PR: provider-independent acquisition runtime core)
Scope: execution-model extraction only. No new platforms, no scheduler, no dashboard.

---

## A. Architecture decision

The TikTok collector proved an execution model that is **not actually TikTok-specific**:
durable pagination state, retry budgets, incremental id-dedup persistence, checkpoint
ordering, metrics, termination taxonomy, and resume. Those primitives were embedded in
`src/tiktok_schema.py` and `src/collector.py`. They are now extracted into
`src/runtime/` — a provider-independent execution core ("mini Apify runtime core"):

```
Actor/Provider  (AcquisitionActor — provider code only)
       ↓
RunContext       (run_id · job_id · provider · target · started_at · paths)
       ↓
AcquisitionRuntime
  ├── job identity            RunContext
  ├── pagination/checkpoint   PaginationState + CheckpointStore (atomic tmp+replace)
  ├── retry policy            budgets in RunOptions → PaginationState counters
  ├── incremental persistence JsonlDataset (append + fsync, id-dedup)
  ├── metrics                 AcquisitionMetrics
  ├── termination reason      TerminationReason strings + Outcome classification
  └── resume support          checkpoint restore + dataset id replay
       ↓
Dataset / raw event sink   (JSONL — canonical schema untouched)
```

New modules (stdlib only):

| module | owns |
|---|---|
| `src/runtime/context.py` | `RunContext`, `utc_now` |
| `src/runtime/termination.py` | `TerminationReason`, `Outcome`, `classify_termination` |
| `src/runtime/metrics.py` | `AcquisitionMetrics` (moved verbatim) |
| `src/runtime/state.py` | `PaginationState`, `PaginationDiagnostic` (moved verbatim) |
| `src/runtime/checkpoint.py` | `CheckpointStore` (extracted from `save_job`/`load_job`) |
| `src/runtime/dataset.py` | `JsonlDataset`, `load_seen_ids`, `append_records` (generalized `append_raw_records`/`write_jsonl`) |
| `src/runtime/actor.py` | `AcquisitionActor` contract + `PageResult` |
| `src/runtime/engine.py` | `AcquisitionRuntime`, `RunOptions`, `RunSummary` |

## B. Exact boundary: runtime vs provider

**Runtime owns (never provider code):**
- the page loop, retry budgets, stall/empty/parse-error counters
- failure classification (fetch / parse / auth-block) via shared `PaginationState`
- persistence ordering invariant: **dataset write BEFORE checkpoint commit**
  (a failed disk write never advances the checkpoint — same gate as TikTok)
- id-dedup against everything already persisted (memory + disk replay)
- item/page caps, termination reason, outcome classification, metrics
- resume: restore state from checkpoint, rebuild seen-ids from dataset

**Actor owns (provider-specific only):**
- auth/session/browser and how to fetch ONE page: `fetch_page(ctx, cursor, page_index) -> PageResult`
- item identity: `id_key` / `item_id(item)`
- starting cursor: `initial_cursor()`
- payload quirks (e.g. TikTok's `cid`/`has_more`/`cursor` shapes)

Actor contract is deliberately minimal — no hooks for transforms, scheduling,
webhooks, storage backends, or concurrency. Failures are reported either by
raising (runtime classifies as `fetch_failure`) or via `PageResult(error=..., error_type=...)`.

## C. Implementation

See `src/runtime/` (above) and `tests/test_acquisition_runtime.py`.
Key engine invariants:
- gross page items are passed to `PaginationState.process_page` (a state API,
  NOT part of the actor contract) with `deduplicated=N`, so a
  fully-duplicated page with an unchanged cursor registers as a **stall**, not an
  empty page (byte-compatible with the TikTok collector's semantics).
- `has_more=None` from an actor is treated as `False` (explicit contract).
- actor exceptions are converted to classified `fetch_failure` page results.
- configured caps (`max_items`, `max_pages`) are **not terminal**: a resumed run
  re-evaluates them against the new budget. `max_items` is a HARD limit enforced
  mid-page: after id-dedup, only the remaining budget's worth of new unique
  items is persisted, then the run stops with `max_comments_reached` (cap
  dominates completion signals like `has_more_false`; genuine failure reasons
  from `process_page` keep priority). Duplicates never consume the cap.

### Item-count sources of truth

Seven counters exist; exactly one is authoritative:

| Counter | Role |
|---|---|
| `dataset.seen_ids` | **SOURCE OF TRUTH** — disk-backed unique ids actually persisted |
| `state.items_seen` | cache, synced to `len(dataset.seen_ids)` via `record_items()` after every batch |
| `metrics.items_unique` | cache, set to the same value by `record_items()` |
| `metrics.items_collected` | telemetry only — cumulative GROSS items fetched (incl. dupes), never authoritative |
| `summary.items_seen` | snapshot of `len(dataset.seen_ids)` at return time |
| `checkpoint.items_seen` (+ `pagination.items_seen`) | snapshot persisted atomically AFTER each dataset write |
| `summary.items_written` | delta appended during THIS invocation only |

Invariant: every cached counter must equal `len(dataset.seen_ids)` at commit/
return boundaries; only `items_collected` (gross) and `items_written` (delta)
intentionally differ.

## D. Tests

`tests/test_acquisition_runtime.py` — 9 deterministic tests (stdlib, no browser):
1. actor can start a run → dataset + atomic (tmp+replace) checkpoint written,
   restorable on resume
2. resume continues from cursor with zero duplicates
3. retry state survives in checkpoint (incl. pending-retry round-trip)
4. termination reason + outcome preserved (auth-block, stall, cap, retry-budget)
5. metrics recorded (attempts, successes, items, errors, duration)
6. duplicate pages/items never corrupt dataset or counts
7. TikTok-shaped payloads (`cid`/`cursor`/`has_more`, incl. transient empty page)
   run on the runtime past the historical 198-comment boundary
8. a second fake provider runs through the *same runtime instance* unmodified
9. resuming an ALREADY-COMPLETED checkpoint: provider not called, dataset
   untouched, termination reason preserved, `items_seen` equals the persisted
   unique record count

## E. Migration of TikTok onto the runtime

- `src/tiktok_schema.py` no longer defines the state/metrics/termination
  primitives; it **re-exports** them from `src/runtime/`. Every existing import
  path (`from src.tiktok_schema import PaginationState, ...`) keeps working and
  checkpoint serialization is byte-identical, so existing TikTok checkpoints on
  disk load unchanged.
- `append_raw_records`/`write_jsonl` now delegate to `src.runtime.dataset`
  (id key pinned to `comment_id`); `save_job`/`load_job` delegate to
  `CheckpointStore`. Behavior is unchanged — verified by the untouched
  pagination + hardening suites.
- The TikTok **browser loop** (`_capture_pass`) intentionally remains
  TikTok-specific (see G); its proven page script is proven equivalent on the
  runtime by the TikTok-shaped actor test.

## F. Proof that current TikTok tests still pass

```
tests/test_tiktok_pagination.py      11 passed, 0 failed
tests/test_acquisition_hardening.py  11 passed, 0 failed
tests/run_dedup_quality_tests.py      8 passed, 0 failed  (exit 0)
tests/test_self_improvement.py       11 passed, 0 failed
tests/test_acquisition_runtime.py     9 passed, 0 failed
python -m py_compile (all runtime/tiktok/collector/test files)  OK
```

## G. Intentionally NOT generalized yet

- **Browser multi-path capture** (DOM scrape + Playwright route intercept +
  proactive API fetch multiplexed in `_capture_pass`) — the actor contract is
  single-cursor; forcing three ingestion paths into it now would be premature.
  TikTok keeps its loop; the runtime proves the same semantics.
- **Reply-thread expansion** (recursive `/comment/list/reply` rounds) —
  platform-specific traversal, not runtime state.
- **Captcha / anti-bot resolution, stealth, humanized delays** — provider
  concerns (`src/harness/`).
- **Canonical schema mapping** — unchanged; runtime sinks raw records, the
  existing `schema/` + `pipeline/` stages consume them as before.
- **Storage backends** beyond append-only JSONL (no SQLite/S3/queues).
- **Scheduling, concurrency, webhooks, dashboards, billing** — explicitly out of scope.
- **Legacy reason strings** (`max_comments_reached`, `has_more_false`, …) are
  kept byte-compatible instead of renamed; `Outcome` provides the clean taxonomy.
- **No second real platform** — only fake/TikTok-shaped actors in tests, per scope.
- **Fresh-run dataset assumption (KNOWN LIMITATION):** a run started WITHOUT
  `resume=True` assumes its `dataset_path` is new/empty. Re-invoking a job on a
  pre-existing dataset path without resume appends duplicate records — the
  engine only replays disk ids on the resume path. Always use a fresh
  `dataset_path`, or `resume=True`, when records already exist.
- **Checkpoint durability is rename-atomic, not fsync-durable:**
  `CheckpointStore.save` writes a temp file then replaces the target, but does
  not fsync file or directory. On power loss the checkpoint may be lost/corrupt;
  `load()` then returns None and the next run starts fresh. Also: no lock —
  concurrent invocations against one job_id are unsupported (single-writer).
- **Error classification is substring-based (inherited verbatim from TikTok):**
  an error string merely *containing* "block"/"verify"/"captcha"/"security" is
  classified terminal `auth_blocked` instead of retryable fetch failure.
  Providers should pass precise `error_type` values to avoid false positives.
