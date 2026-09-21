# Social Data Engine

A research-oriented social data acquisition and processing pipeline for computational social science.

The project currently stabilizes **TikTok acquisition first**, while the architecture is designed to support additional providers later without changing the canonical downstream data model.

> **Current status:** TikTok comment acquisition is live-verified across multiple pages, including the historical ~198-comment failure boundary. PR #2 is still open and has not been merged yet.

## Quickstart (baru clone? mulai di sini)

```bash
# 1. Environment
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt

# 2. Verifikasi semua gate (compile, tests, security, deps — ±1 menit)
bash scripts/preflight.sh .venv/bin/python

# 3. Test suite deterministik (tanpa browser, tanpa network)
.venv/bin/python tests/test_pipeline.py

# 4. Koleksi live (butuh browser + login — lihat "Live test" di bawah)
bash run.sh "https://www.tiktok.com/@user/video/ID" --max 50 --scrolls 40
```

Output koleksi mendarat di `data/{raw,curated,normalized}/<YYYY-MM-DD>/<video_id>.jsonl`.

**Kalau kamu seorang agent (atau mau men-review patch agent):** baca
[`docs/AGENT-GUIDE.md`](docs/AGENT-GUIDE.md) (peta repo, aturan yang di-enforce,
workflow kontribusi) dan [`agents.md`](agents.md) (checklist pre-merge + prosedur
live test human-in-the-loop). Semua dokumen desain ada di [`docs/`](docs/).

**Live test butuh desktop**, bukan server headless: Chrome/Brave dengan
`--remote-debugging-port=9222 --remote-allow-origins=*`, lalu
`bash scripts/test_live.sh` (pre-flight) sebelum `run.sh`. Detail:
`agents.md §Live Test Procedure`.

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
│   (the CLI runs `pipeline/canonical_runner.py` — the single stage
│    implementation; `pipeline/legacy.py` is only a re-export shim)
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
├── test_plugins.py            # External provider plugin contract
├── test_mcp_server.py         # MCP stdio protocol surface
├── test_mcp_http_security.py  # Streamable-HTTP transport + hardening refusals
├── test_plugin_host.py        # Generic plugin package (manifest + JS adapter + installer)
├── test_run_status.py         # Run/provenance reader + manifest.v1 stamp
├── test_import_layering.py    # Provider-blind runtime import budget (NFR-007)
└── run_dedup_quality_tests.py

integrations/
└── plugin/                   # This engine as a host-agnostic plugin folder (manifest + JS adapter + installer)

data/                         # Runtime / sample datasets
state/                         # Local job checkpoints
logs/                          # Runtime logs
docs/                          # Design, verification and versioning docs
```

`src/pipeline/canonical_runner.py` is the single implementation of every stage (normalize → dedup → quality → enrich). `src/pipeline/legacy.py` (the original single-file pipeline, formerly `src/pipeline.py`) remains only as a re-export shim for compatibility: `from src import pipeline` keeps working (`pipeline.run_video`, `pipeline.RAW_DIR`, …). Do not edit stages in two places — edit `canonical_runner.py`.

All design documentation lives under `docs/` (product/spec: `docs/PRODUCT.md`, `docs/PRD.md`, `docs/SRS.md`, `docs/SDD.md`; status: `docs/CURRENT-STATE.md`, `docs/ROADMAP.md`; governance: `docs/DECISIONS.md`, `docs/ENGINEERING_CONSTITUTION.md`, `policies/`; agent onboarding: `docs/AGENT-GUIDE.md`).

## Pluggable providers

Platform baru = satu plugin, tanpa mengubah core:

```bash
cp -r plugins/example plugins/myplatform   # edit URL_PATTERN, probe(), collect()
.venv/bin/python -m src.tiktok_linkedin --list-plugins
.venv/bin/python -m src.tiktok_linkedin "https://myplatform.com/post/1"
```

Semua provider mengembalikan canonical `Observation` lewat satu registry
(`src/harness/registry.py`) — routing by URL regex, introspeksi via
`--list-plugins`. Panduan lengkap: `docs/AGENT-GUIDE.md §Pluggable providers`.

## Host integration — SDE as a plugin

SDE juga bisa dipakai **dari dalam** host lain. Empat tool yang sama
(`sde_list_providers`, `sde_probe`, `sde_collect`, `sde_run_status`) tersedia lewat
tiga jalur, dan **tidak ada satu host pun yang di-hardcode** di repo ini:

```bash
# A. stdio MCP (host MCP standar) — direkomendasikan: tanpa socket, tanpa timeout bridge
.venv/bin/python -m src.mcp_server

# B. MCP over Streamable HTTP (host yang hanya bisa URL)
.venv/bin/python -m src.mcp_http --port 8765     # loopback-only secara default

# C. folder plugin generik
bash integrations/plugin/install.sh --dir <folder-plugin-host> --dry-run
```

`index.js` hanya adapter transport: tidak ada logika routing/validasi kedua di JS,
hanya `node:` builtin, tidak pernah lewat shell. Postur keamanan bridge, contoh
adapter untuk host yang menuntut format manifest sendiri, dan batasan jujurnya
(termasuk host yang **belum** diverifikasi):
[`docs/INTEGRATIONS/PLUGIN.md`](docs/INTEGRATIONS/PLUGIN.md).

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

Current local verification reported (25 suites, 282 assertions):

```text
test_acquisition_hardening.py   11 passed
test_acquisition_runtime.py     15 passed
test_actor_harness.py           22 passed
test_canonical_roundtrip.py      8 passed
test_checkpoint_fail_closed.py   4 passed
test_dedup.py                    4 passed
test_dedup_scaling.py            6 passed
test_import_layering.py          9 passed
test_input_validation.py        15 passed
test_loop_state.py              13 passed
test_mcp_http_security.py       21 passed
test_mcp_server.py              14 passed
test_pipeline.py                13 passed
test_plugin_host.py             17 passed
test_plugins.py                  7 passed
test_policy_evaluator.py        18 passed
test_policy_models.py            8 passed
test_provider_asset_hygiene.py   5 passed
test_run_status.py              12 passed
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

### Plugin/host startup cost

A host spawns this engine once per tool call, so import cost is user-visible. The
host path (`src/mcp_server.py`) no longer drags in the browser stack: importing it
is **51 ms** (was 80 ms), a one-shot `tools/list` round-trip is **51 ms** (was
85 ms), and `playwright`/`camoufox`/`asyncio` are not loaded at all — which also
means listing tools and probing still work in an environment without the optional
browser packages. Enforced by `tests/test_import_layering.py`; measurements in
`docs/VERIFICATION.md` §15.

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

### Next (owner: `docs/ROADMAP.md` — Phase 2 “Enforcement & trust”)

- [x] Strengthen nested reply collection and thread completeness — thread-aware dedup keeps replies to different parents, `src/pipeline/thread_builder.py` rebuilds conversation trees, and `scripts/trace_comment.py --trace-all` traces 44/44 curated records
- [x] Improve provider contract tests — `tests/test_actor_harness.py` (22, fake + TikTok-shaped actors through one runtime) and `tests/test_provider_asset_hygiene.py` (5, provider registration/routing + asset hygiene)
- [ ] Add richer dataset manifests and reproducibility metadata — the `manifest.v1` schema stamp and `sde_run_status` inspection landed 2026-09-21; manifest writing at every dataset append is still Phase 2 work (FR-PROV-003/004)
- [x] Stabilize the canonical pipeline boundary before adding more providers — the provider-blind runtime import audit (NFR-007) is now an enforced test (`tests/test_import_layering.py`), not a documented intention
- [ ] Add additional social platforms only after the MVP acquisition layer is stable — the second real provider is Phase 5
- [ ] Live gate: coverage ≥95 % on a logged-in desktop run (guest sessions plateau at 22–36 %) — Phase 3, requires the human-in-the-loop run in `agents.md`

## License

MIT
