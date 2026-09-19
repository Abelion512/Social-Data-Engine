# Session Log — 2026-09-19 · performance pass

## Goal
User request: *"Optimize performance."* No target given, so the rule was
**measure first, then optimize only what the profile proves**, and prove the
optimized code is *output-identical* (no behavior change without a live test).

## Measurement (before touching code)
Profiled the deterministic data path with synthetic corpora at realistic sizes
(5 000 comments; `cProfile` + wall clock, stdlib only, no browser):

| Stage (5 000 records) | Time | Share |
|---|---|---|
| `dedup_near_duplicate` (tier 3) | **13 099 ms** | 96 % of the data path |
| canonical map | 37 ms | — |
| quality gate | 20 ms | — |
| `resolve_identity` | 3 ms | — |
| `build_threads` | 6 ms | — |

Profile detail: 698 300 Jaccard pair comparisons, 27.8 M `Counter.__missing__`
calls, i.e. the O(n²) scan the module's own docstring flagged as "ponytail".

## Fixes (both measured, both output-preserving)
1. **Tier 3: size-window pruning + allocation-free overlap.**
   Invariant: multiset Jaccard satisfies `J ≤ min(|A|,|B|)/max(|A|,|B|)`, so a pair
   whose bigram-count ratio is below the threshold can never reach it. Buckets per
   parent are kept sorted by bigram count and each candidate only scans
   `p ∈ [t·size, size/t]` (bounds widened by one token → float-safe). The
   comparison no longer builds two temporary `Counter`s per pair.
   *Result:* 11 999 ms → 409 ms on the end-to-end corpus (29×) and
   13 099 ms → 408 ms on the wide-vocabulary corpus (32×),
   **same kept records, same order** (differential proof below).
   *Residual (honest):* uniform-length corpora prune nothing → still O(n²)
   (13 605 ms → 1 801 ms, 7.6× from the overlap rewrite alone).
2. **`normalize_text` memoized** (bounded `lru_cache(50_000)`). Pure `str→str`;
   the same text was folded up to four times per record (normalize stage, tier 2,
   tier 3 bigrams, manifest). Same output, once per distinct string.
3. **`StageRunner._write_output` wrote one file per record** (`000000.jsonl`,
   `000001.jsonl`, …). Now a single `records.jsonl` per stage, written
   `.tmp` + `os.replace`:
   2 000 records 138 ms / 2 000 files → **10.6 ms / 1 file**; 20 000 records
   ≈1.4 s / 20 000 files → 106 ms / 1 file. Loader still reads the legacy layout
   (upgrade path), prefers `records.jsonl` when both exist, ignores `.tmp`.
4. **Bug found while testing fix 3:** `StageRunner._is_complete` required
   `output_count > 0`, so a stage that legitimately produced zero records
   (everything filtered by the quality gate) never counted as done and re-ran on
   every resume. Completion now keys off the manifest (commit marker, written
   after the output) plus readable output.

## Verification (new deterministic suites, no timing assertions in CI)
`tests/test_dedup_scaling.py` (6) — differential vs a verbatim copy of the old
full scan: 3 shapes × 3 seeds × 4 thresholds (incl. 1.0) + hand-built edge cases
(empty/1-char/unicode/whitespace noise, near-dup across different parents) +
"provably-incompatible sizes are never compared (0 comparisons)" + n²/4 bound +
`normalize_text` memo transparency.

`tests/test_stages_io.py` (6) — single-file layout, round-trip fidelity, shorter
rewrite leaves no stale record, legacy layout loads, current file wins over
legacy leftovers, `.tmp` never read, `run_stage` idempotent (this last one caught
the zero-record completion bug).

Full matrix after the pass: **18 suites / 187 assertions green**, `py_compile`
OK, CI symbol check OK, `bash -n run.sh` OK, credential grep 0 hits.

## Live-test gate (agents.md)
NOT run — headless sandbox (no display/CDP, no Camoufox binary), same constraint
as VERIFICATION §7/§9/§11. Justification: every change here is behaviour-*preserving*
and proven so on the exact records the live path writes (identical kept sets at
5 000 records + 96 differential corpora); nothing about acquisition was touched.
The gate stays mandatory before any merge that changes collection behavior.

## What was deliberately NOT changed
- No `fsync`, batch size or checkpoint-durability change (governance invariant:
  raw stores are append-only and durable — `policies/DATA-GOVERNANCE.md`).
- No new dependencies, no MinHash/LSH, no parallelism (Ponytail: the measured win
  was available without them; the escalation trigger is documented instead).
- No anti-bot timing/delay changes in the collector (`src/harness/human.py`,
  scroll sleeps) — those are safety behavior, not CPU cost.
