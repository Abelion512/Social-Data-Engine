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
│   ├── identity.py            # Cross-platform identity resolution
│   ├── stages.py              # Resumable stage runner
│   └── improve.py             # Deterministic self-improvement loop
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
├── test_tiktok_pagination.py # Pagination / checkpoint invariants
├── test_acquisition_hardening.py
├── test_acquisition_runtime.py # Provider-independent runtime core tests
├── test_dedup.py
├── test_self_improvement.py
└── run_dedup_quality_tests.py

data/                         # Runtime / sample datasets
state/                         # Local job checkpoints
logs/                          # Runtime logs
docs/                          # Design, verification and versioning docs
```

`src/pipeline.py` and several older entry points remain for compatibility with the previous TikTok pipeline. New development should use the provider/canonical pipeline interfaces where available.

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

```bash
source .venv/bin/activate

python -m pytest tests/test_tiktok_pagination.py -v
python -m pytest tests/test_acquisition_hardening.py -v
python -m pytest tests/test_acquisition_runtime.py -v
python -m pytest tests/test_dedup.py -v
python -m pytest tests/test_self_improvement.py -v
python tests/run_dedup_quality_tests.py
```

Current local verification reported:

```text
test_tiktok_pagination.py       11 passed
test_acquisition_hardening.py   11 passed
run_dedup_quality_tests.py      41 passed
test_self_improvement.py         8 passed
```

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
python src/pipeline.py --video 123
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

## CI

The repository CI checks:

| Gate | Verification |
|---|---|
| Compile | Python bytecode compilation |
| Import | Module / symbol compatibility checks |
| Shell | Bash + Zsh syntax |
| Tests | Deterministic pipeline test suites |
| Security | Credential-name / secret hygiene checks |
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

### Next

- [ ] Strengthen nested reply collection and thread completeness
- [ ] Add richer dataset manifests and reproducibility metadata
- [ ] Improve provider contract tests
- [ ] Stabilize the canonical pipeline boundary before adding more providers
- [ ] Add additional social platforms only after the MVP acquisition layer is stable

## License

MIT
