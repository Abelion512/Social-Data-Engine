# Social Data Engine

A research-oriented social data acquisition and processing pipeline for computational social science.

The project currently stabilizes **TikTok acquisition first**, while the architecture is designed to support additional providers later without changing the canonical downstream data model.

> **Current status:** TikTok comment acquisition is live-verified across multiple pages, including the historical ~198-comment failure boundary. PR #2 is still open and has not been merged yet.

## What it does

```text
platform
   ↓
provider acquisition
   ↓
raw events / comments
   ↓
normalization
   ↓
deduplication
   ↓
quality gate
   ↓
canonical observations
   ↓
export / downstream consumers
```

The main design goal is **reproducible acquisition**, not merely scraping a page once. Collection state, pagination progress, provenance, deduplication, and failure reasons are explicit so a run can be inspected or resumed.

## Architecture

### Acquisition

TikTok acquisition uses one authoritative pagination state machine.

```text
TikTok session
    │
    ├── proactive API pagination
    │      cursor=0 → 50 → 100 → 150 → ...
    │
    ├── passive Playwright route capture
    │
    └── passive DOM capture
             │
             ▼
       acquisition buffer
             │
             ▼
     PaginationState
       ├─ cursor
       ├─ page_index
       ├─ has_more
       ├─ retry_count
       ├─ termination_reason
       └─ diagnostics / metrics
             │
             ▼
      incremental raw write
             │
             ▼
        checkpoint save
```

The proactive API path is the authoritative pagination mechanism. Route and DOM capture are ingestion/fallback paths and do not independently advance the pagination cursor.

Each successful batch is persisted before the corresponding checkpoint is committed. This makes partial progress durable across interruption and resume.

### Processing pipeline

```text
collect
  → raw
  → normalize
  → dedup
  → quality gate
  → canonicalize
  → annotate / verify
  → curated
  → export
```

Pipeline stages are designed to be resumable and idempotent where practical.

## Repository layout

```text
src/
├── collector.py              # TikTok acquisition engine
├── tiktok_schema.py          # Raw TikTok schema (re-exports runtime primitives)
├── runtime/                  # Provider-independent acquisition runtime core
│   ├── context.py            # RunContext (run/job identity, paths)
│   ├── state.py              # Pagination/retry/termination state
│   ├── metrics.py            # AcquisitionMetrics
│   ├── checkpoint.py         # Atomic checkpoint store
│   ├── dataset.py            # Id-dedup JSONL event sink
│   ├── actor.py              # AcquisitionActor contract
│   ├── engine.py             # AcquisitionRuntime loop
│   └── termination.py        # Termination taxonomy / outcome classification
├── providers/
│   ├── base.py               # Provider interfaces
│   └── tiktok.py              # TikTok provider adapter
├── schema/
│   ├── canonical.py          # Canonical observation model
│   └── mapper.py              # Provider → canonical mapping
├── pipeline/
│   ├── dedup.py              # Exact / normalized / near-duplicate dedup
│   ├── quality.py             # Quality scoring and gating
│   ├── identity.py            # Identity resolution (one Entity per provider:author)
│   ├── stages.py              # Resumable stage runner
│   └── improve.py             # Deterministic self-improvement loop
│
│   (the CLI runs `pipeline/legacy.py`'s inline stages; the modular modules
│    above are the tested refactor layer, not yet wired — CURRENT-STATE §6.8)
├── export/
│   ├── mark.py                # MARK Agent export
│   └── manifest.py            # Dataset / pipeline manifest generation
├── harness/                   # Agent / browser tooling
├── config.py                  # Runtime configuration
└── browser_selector.py        # Browser backend selection

scripts/
├── test_live.sh              # Live verification entry point
├── benchmark.py              # Benchmark utilities
├── trace_comment.py          # Acquisition diagnostics
└── ...                       # Browser / environment probes

tests/
├── test_tiktok_pagination.py  # Pagination / checkpoint invariants
├── test_acquisition_hardening.py
├── test_acquisition_runtime.py # Provider-independent runtime core tests
├── test_checkpoint_fail_closed.py
├── test_actor_harness.py      # Harness / actor contract + identity binding
├── test_policy_models.py      # Policy vocabulary + bounded budgets
├── test_policy_evaluator.py   # Deny-by-default evaluation table
├── test_security_gates_sg2.py # Identity-safe ids + rooted path resolution
├── test_loop_state.py         # Durable loop state / resume semantics
├── test_canonical_roundtrip.py # Canonical persist → load fidelity
├── test_pipeline.py
├── test_dedup.py
├── test_self_improvement.py
├── test_thread_builder.py    # Reply-tree reconstruction invariants
├── test_provider_asset_hygiene.py # Provider/asset regression guards
├── test_input_validation.py  # Traversal / argv-injection / limit enforcement
└── run_dedup_quality_tests.py

data/                         # Runtime / sample datasets
state/                         # Local job checkpoints
logs/                          # Runtime logs
docs/                          # Design, verification and versioning docs
```

`src/pipeline/legacy.py` (the original single-file pipeline, formerly `src/pipeline.py`) and several older entry points remain for compatibility with the previous TikTok pipeline. The `src/pipeline/` package re-exports its public surface, so `from src import pipeline` keeps working (`pipeline.run_video`, `pipeline.RAW_DIR`, …). New development should use the provider/canonical pipeline interfaces where available.

## Canonical data model

The canonical layer is defined in `src/schema/canonical.py` and separates observed data from derived interpretation.

| Entity | Purpose |
|---|---|
| `Observation` | One social observation such as a comment or reply |
| `Content` | Raw and normalized text without destructive replacement |
| `Entity` | Author or other identifiable entity |
| `Relationship` | Reply, mention, co-occurrence, and similar links |
| `Annotation` | Derived labels or model output, kept separate from observed data |
| `Evidence` | Evidence supporting an annotation or inference |
| `Provenance` | Collector, pipeline, timestamp, model and source metadata |
| `Confidence` | Confidence value, method and supporting evidence |

The separation matters for later research and model-training workflows: raw observations should remain recoverable even when downstream annotation logic changes.

## Pagination, checkpoints and failure handling

The acquisition engine treats these as first-class states rather than incidental exceptions:

- cursor advancement
- repeated cursor / pagination stall
- transient empty pages
- fetch failures and retries
- parse failures
- authentication / anti-bot blocking
- maximum collection caps
- normal `has_more=false` completion

Each run records structured acquisition metrics and diagnostics. A checkpoint contains enough state to resume from the last durable cursor rather than restarting the entire collection.

## Live verification

The historical failure mode was a stall around **198 comments**. The current implementation changed the proactive TikTok request from an invalid oversized batch to the observed web API batch size of **50 comments per request** and made pagination state explicit.

Live verification on the historical target demonstrated:

```text
cursor=150 → next=200   unique: 146 → 196
cursor=200 → next=250   unique: 196 → 245
cursor=250 → next=251   unique: 245 → 246, has_more=false
```

Additional live verification reported:

- TikTok photo post: **71 / 71 comments** collected
- TikTok video target: **246 / 246 comments** collected
- Resume test: checkpoint at cursor `150`, then resumed to completion without duplicate IDs

These are live verification results from the development environment. CI currently focuses on deterministic repository checks and does not require authenticated TikTok access.

## Testing

### Deterministic tests

The suites are self-contained scripts (stdlib-only, no `pytest`, no network or
display required). Run each one directly — every suite exits non-zero on
failure:

```bash
python tests/test_tiktok_pagination.py
python tests/test_acquisition_runtime.py
python tests/test_policy_evaluator.py
# ... or run them all, the way CI does:
for suite in tests/test_*.py tests/run_*_tests.py; do python "$suite" || break; done
```

Current local verification reported (19 suites, 202 assertions):

```text
test_acquisition_hardening.py   11 passed
test_acquisition_runtime.py     15 passed
test_actor_harness.py           22 passed
test_canonical_roundtrip.py      8 passed
test_checkpoint_fail_closed.py   4 passed
test_dedup.py                    4 passed
test_dedup_scaling.py            6 passed
test_input_validation.py        15 passed
test_loop_state.py              13 passed
test_pipeline.py                13 passed
test_policy_evaluator.py        18 passed
test_policy_models.py            8 passed
test_provider_asset_hygiene.py   5 passed
test_security_gates_sg2.py      17 passed
test_self_improvement.py        11 passed
test_stages_io.py                6 passed
test_thread_builder.py           7 passed
test_tiktok_pagination.py       11 passed
run_dedup_quality_tests.py       8 passed
```

### Processing throughput

The deterministic data path (normalize → canonical map → 3-tier dedup → quality
gate → JSONL) processes 5 000 comments in ≈0.6 s on a laptop-class CPU. The
near-duplicate tier no longer compares all pairs — it prunes on a provably-safe
character-bigram size window (`J ≤ min(|A|,|B|)/max(|A|,|B|)`), which took that
tier from ≈12 s to ≈0.4 s at 5 000 records with an output-identical result set
(differential tests in `tests/test_dedup_scaling.py`, numbers in
`docs/VERIFICATION.md` §12).

### Live test

Live collection requires an authenticated browser session and should be treated separately from deterministic CI tests.

```bash
bash scripts/test_live.sh
```

For debugging, use the acquisition trace tooling and inspect `logs/`, `state/`, and the generated `data/raw/` records.

## Usage

### Provider API

```python
import asyncio
from src.providers.tiktok import TikTokAdapter

adapter = TikTokAdapter()
observations = asyncio.run(
    adapter.collect(
        "https://www.tiktok.com/@user/video/123",
        max=500,
    )
)
```

### CLI / legacy compatibility

```bash
source .venv/bin/activate

bash run.sh "https://www.tiktok.com/@user/video/123" --max 100
python src/pipeline/legacy.py --video 123        # or: python -m src.pipeline.legacy --video 123
```

### Export

```python
from src.export.mark import export_video
export_video("123")
```

## Authentication

The project uses **user-provided browser sessions / cookies** rather than password automation.

Typical flow:

```text
manual browser login
      ↓
persistent browser profile
      ↓
reused session cookies
      ↓
authenticated collection
```

Credentials and session state are kept outside the repository. Never commit cookies, API keys, passwords, or browser profiles.

## Auto / Recursive Self-Improvement

`src/pipeline/improve.py` contains a deterministic feedback loop for pipeline remediation.

```text
observe → plan → act → re-collect → evaluate
   ↑                              │
   └────────── iteration ─────────┘
```

This is an architectural control loop, not a trained model. It uses explicit metrics and bounded iterations to react to collection problems without silently mutating arbitrary behavior.

See `docs/SELF-IMPROVEMENT.md` for the current design.

## Deduplication

The pipeline supports multiple deduplication tiers:

1. exact identity / exact text
2. normalized text comparison
3. near-duplicate detection using character bigram similarity

Deduplication is applied without deleting the original raw records, preserving traceability.

## Provenance and versioning

The data model carries provenance so a downstream result can be traced back to the collection and processing versions that produced it.

See:

- `docs/IMPLEMENTATION.md`
- `docs/VERIFICATION.md`
- `docs/VERSIONING.md`
- `docs/SELF-IMPROVEMENT.md`
- `docs/PONYTAIL.md` (the ladder, `ponytail:` ceilings, debt ledger)

## CI

The repository CI checks:

| Gate | Verification |
|---|---|
| Compile | Python bytecode compilation |
| Import | Module / symbol compatibility checks |
| Shell | Bash + Zsh syntax |
| Tests | Deterministic pipeline test suites |
| Security | Credential-name / secret hygiene checks |
| Ponytail | Deferred-work markers (`TODO`/`FIXME`/`XXX`/`HACK`) rejected in `src/` + `scripts/` |
| Versioning | Semver and provider-scope rules |

Authenticated live TikTok tests are intentionally not required by CI because they depend on external browser state and platform conditions.

## Roadmap

### Current

- [x] TikTok comment acquisition
- [x] Cursor-based pagination
- [x] Durable checkpoints and resume
- [x] Incremental raw persistence
- [x] Multi-tier deduplication
- [x] Canonical schema
- [x] Deterministic acquisition hardening tests
- [x] Live validation beyond the historical ~198-comment boundary
- [x] Whole-repo ponytail audit (dead code, dead imports, re-export boilerplate) — `docs/PONYTAIL.md`, record in `docs/VERIFICATION.md` §14

### Next (owner: `ROADMAP.md` — Phase 2 “Enforcement & trust”)

- [x] Strengthen nested reply collection and thread completeness — thread-aware dedup keeps replies to different parents, `src/pipeline/thread_builder.py` rebuilds conversation trees, and `scripts/trace_comment.py --trace-all` traces 44/44 curated records
- [x] Improve provider contract tests — `tests/test_actor_harness.py` (22, fake + TikTok-shaped actors through one runtime) and `tests/test_provider_asset_hygiene.py` (5, provider registration/routing + asset hygiene)
- [ ] Add richer dataset manifests and reproducibility metadata — manifest schema v1 + version stamps are Phase 2 work (FR-PROV-003/004)
- [ ] Stabilize the canonical pipeline boundary before adding more providers — the provider-blind runtime import audit test (NFR-007) is scheduled for Phase 2
- [ ] Add additional social platforms only after the MVP acquisition layer is stable — the second real provider is Phase 5
- [ ] Live gate: coverage ≥95 % on a logged-in desktop run (guest sessions plateau at 22–36 %) — Phase 3, requires the human-in-the-loop run in `agents.md`

## License

MIT
