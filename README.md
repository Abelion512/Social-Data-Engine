# Social Data Engine

[![CI](https://github.com/Israfelse/social-data-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Israfelse/social-data-engine/actions/workflows/ci.yml)

Multi-provider digital behavioral data platform untuk computational social science.

## Visi

Mengumpulkan, menormalisasi, mendedup, dan mengekspor data sosial — mulai dari TikTok, LinkedIn, YouTube, Reddit — ke format kanonikal yang siap untuk analisis, RAG, dan consumer downstream (MARK Agent, LinkedIn outreach, dll.).

## Arsitektur

```
run.sh                    # Launcher utama (log otomatis → logs/)
.env                      # Config (gitignored — 9Router API key, model, dsb.)
├─ NEW architecture (recommended)
src/
├── __init__.py
├── browser_selector.py     # Pilih browser driver (nodriver/camoufox/playwright)
├── config.py               # LLM/credential config (9Router)
├── export_tiktok_cookies.py # Export TikTok cookies (Camoufox) → JSON
├── export/                 # Export sub-package
│   ├── __init__.py
│   ├── mark.py             # MARK Agent JSON export (video + corpus)
│   └── manifest.py         # Pipeline metadata generation
├── pipeline/               # Pipeline sub-modules (idempotent stages)
│   ├── __init__.py
│   ├── dedup.py            # Multi-tier: exact → normalized → near-duplicate (Jaccard bigram)
│   ├── quality.py          # Heuristic quality scoring + gating
│   ├── identity.py         # Cross-platform identity resolution
│   └── stages.py           # Idempotent stage runner with resume
├── providers/              # Provider adapters (multi-provider)
│   ├── base.py             # ProviderAdapter interface (provider_name, collect, probe)
│   └── tiktok.py           # TikTok adapter (Camoufox-based)
└── schema/                 # Canonical data model (shared across providers)
    ├── __init__.py
    ├── canonical.py        # Observation, Entity, Content, Relationship, Annotation, Evidence, Provenance, Confidence
    └── mapper.py           # TikTok → canonical mapper
├─ LEGACY (compat — superseded by adapters above)
src/
├── collector.py            # Camoufox-based browser collector (legacy compat)
├── tiktok_schema.py        # Legacy TikTok schema (raw/normalized/enriched/curated v1)
├── tiktok_linkedin.py      # Legacy entrypoint CLI: chain penuh TikTok → LinkedIn
├── linkedin_consumer.py    # Consumer LinkedIn (baca curated → match/connect)
├── pipeline.py             # Legacy pipeline: normalize → dedup → enrich → quality → manifest
└── mark_export.py          # Legacy MARK export (moved to src/export/mark.py)
├─ Utilitas / probe (opsional)
scripts/
├── export_tiktok_cookies_nodriver.py  # Export cookies via nodriver (alt)
├── camoufox_cycle.py       # Cycle Camoufox profile
├── camoufox_collector.py   # Camoufox-based data collector
├── probe_camoufox.py       # Probe Camoufox setup
├── probe_playwright.py     # Probe Playwright setup
└── vision_collector.py     # Vision collection + LLM analysis
├─ Tests
tests/
├── test_dedup.py            # Dedup unit tests
├── test_pipeline.py         # Pipeline unit tests
└── run_dedup_quality_tests.py  # Self-contained runner (8 check)
├─ Output (gitignored)
data/                       # output pipeline (raw/normalized/dedup/curated/exports)
state/                      # job checkpoint + hasil LinkedIn (CSV)
logs/                       # pipeline_*.log archive
├─ Docs
docs/                       # Design: chatgpt-response.md, IMPLEMENTATION.md, GOAL-EVIDENCE.md, VERIFICATION.md
└─ Memory
.remember/                  # Agent session memory (dipertahankan)
```

## Pipeline

```
collect → raw → normalize → dedup → quality gate → annotation → verification → curated → export
```

Setiap stage idempotent — cek manifest existence + record count sebelum jalan.
Lihat `docs/IMPLEMENTATION.md` (mapping 16-poin → modul) dan
`docs/VERIFICATION.md` (cross-check kode nyata vs klaim) untuk detail.

## Canonical Data Model

Di `src/schema/canonical.py`, lima belas entitas inti (`chatgpt-response.md §Core data model`):

| Entitas | Peran |
|---|---|
| `Observation` | Unit data satuan (comment/reply) — `observation_id`, `source`, `content`, `provenance`, `confidence` |
| `Content` | `text_raw` + `text_normalized` — tidak pernah destructively clean |
| `Entity` | Identitas lintas-platform (`provider_ids`, `display_name`) |
| `Relationship` | Ikatan antar-entitas (reply, mention, co-occurrence) |
| `Annotation` | Catatan LLM (label, skor) — dipisahkan dari observed data |
| `Evidence` | Jejak fakta yang mendukung sebuah klaim/annotasi |
| `Provenance` | `collector_version`, `pipeline_version`, `captured_at`, `processed_at`, `model` |
| `Confidence` | Nilai + metode + evidence untuk setiap peradilan/identity match |

## Design Docs

| Dokumen | Isi |
|---|---|
| `docs/chatgpt-response.md` | Design brief — visi Social Data Engine, arsitektur, batasan etika |
| `docs/IMPLEMENTATION.md` | Mapping 16-poin review → modul + status + bukti live |
| `docs/GOAL-EVIDENCE.md` | Verifikasi 3 elemen goal secara objektif |
| `docs/VERIFICATION.md` | Cross-check kode vs dokumen (build/test matrix + bug fix record) |
| `docs/SELF-IMPROVEMENT.md` | Auto/Recursive Self-Improvement architecture (feedback loop) |
| `docs/VERSIONING.md` | Semantik MVP v1 (TikTok+LinkedIn, kriteria stabil, pre-release checklist) |
| `docs/superpowers/plans/2026-08-19-social-data-engine.md` | Rencana evolusi multi-provider |

## Testing

```bash
source .venv/bin/activate

# Self-contained runner (8 check, tidak butuh pytest)
python tests/run_dedup_quality_tests.py

# Pytest (jika terpasang)
python -m pytest tests/ -v
```

## Usage

```bash
# Aktifkan virtualenv
source .venv/bin/activate

# Launcher (log otomatis masuk logs/)
bash run.sh                                  # login mode
bash run.sh "https://www.tiktok.com/@user/video/123" --max 100
bash run.sh "https://..." --connect          # auto-connect (butuh approval)

# Koleksi TikTok satu video (via new adapter)
python -c "
import asyncio
from src.providers.tiktok import TikTokAdapter
adapter = TikTokAdapter()
observations = asyncio.run(adapter.collect('https://www.tiktok.com/@user/video/123', max=100))
"

# Pipeline penuh raw → curated (legacy)
python src/pipeline.py --video 123

# Export ke MARK Agent
python -c "
from src.export.mark import export_video
export_video('123')
"

# Build manifest
python -c "
from pathlib import Path
from src.export.manifest import build_manifest, write_manifest
m = build_manifest(Path('.'))
write_manifest(Path('.'), m)
"

# Probe / utilitas
python scripts/probe_camoufox.py
python scripts/probe_playwright.py
```

## Authentication (Cookie-Based — never password-based)

**Philosophy:** platform ini **tidak pernah login by password**. Semua otentikasi
bergantung pada **cookies session yang di-capture via browser user sendiri** (CDP),
dengan scope & manajemen yang transparan & safety.

Alur:

1. **Login manual satu kali** — jalankan `bash run.sh` (mode login). Browser
   (Camoufox/nodriver/Playwright) membuka halaman login TikTok/LinkedIn; **user yang
   login sendiri**. Session tersimpan persisten di Chrome profile
   (`~/.tiktok-linkedin/chrome-profile/`).
2. **Reuse cookies** — run berikutnya otomatis memakai profile yang sama, sehingga
   cookies session dipakai kembali. Password tidak pernah disentuh.
3. **Export cookies** (opsional, untuk Electron/Mark session) —
   `python src/export_tiktok_cookies.py` (Camoufox, rekomendasi) atau
   `python scripts/export_tiktok_cookies_nodriver.py` (nodriver alt). Output JSON
   hanya berisi field yang diperlukan:
   `name, value, domain, path, secure, httpOnly, sameSite, expirationDate`.

Transparansi / safety:

- `LINKEDIN_USERNAME` / `LINKEDIN_PASSWORD` **tidak ada di kode** dan **tidak
  di-load** di `run.sh` (dihapus sebagai relik yang berisiko kebocoran).
- Hanya `NINEROUTER_API_KEY` yang dimuat dari `~/.hermes/.env` (9Router proxy key,
  bukan kredensial platform).
- Cookie hanya dipakai untuk request yang sama ke platform (TikTok/LinkedIn) yang
  dibutuhkan untuk koleksi; tidak dikirim ke luar.
- Scope minimal: collector hanya membaca halaman yang diminta, tidak menyurvei
  akun lain, tidak mengubah password/akun.

## Auto / Recursive Self-Improvement

Pipeline **memperbaiki dirinya sendiri** (bukan model) — arsitektur *feedback loop*
yang observasi hasil collect, detect regresi/stall, plan aksi remediatif
**deterministik**, apply, re-run, dan evaluasi — merekrut sampai stabil atau budget
iterasi habis. Lihat `docs/SELF-IMPROVEMENT.md` + `src/pipeline/improve.py`.

```
observe → plan(deteministic) → act(collector overrides) → re-collect → evaluate
            │                                                            │
            └─ iter ≤ max_iter ──> stable? ──ya──► done                  │
                                                        ──tidak─► loop
```

Stabil = coverage ≥ 95 %, semua reply/caption/photo/sticker ter-normalize,
quality gate lolal (lihat `docs/VERSIONING.md` §Kriteria stabil). Setiap iterasi
dicatat ke `data/manifests/<video_id>.improve.jsonl` (audit trail).

```python
from src.pipeline.improve import SelfHealingPipeline, ImprovementPlanner
# runner.run(video_id, url) — observe → plan → act → re-collect → evaluate
```

## Model (9Router)

Config via UI (Configuration page). No `.env`, no `config.yaml`.
- Primary: 9Router proxy (`http://localhost:20128`)
- Fallback: LM Studio (`http://localhost:1234`)
- Model: `abelink` (DeepSeek V4 Flash + Nemotron + Mimo 2.5 composite)

## Schema Versioning

- `raw.v1` — apa yang dikumpulkan (preserved, non-destructive)
- `normalized.v1` — text_normalized + context snapshot
- `enriched.v1` — quality score + identity
- `curated.v1` — lolos quality gate

`text_raw` + `text_normalized` always coexist — tidak ada destructive cleaning.

## Multi-Tier Dedup

1. **Exact** — hash text mentah
2. **Normalized** — lowercase + unicode/whitespace fold
3. **Near-duplicate** — Jaccard similarity pada karakter bigram (threshold 0.85)

## Collection completeness

Collector melaporkan `reported` vs `captured` + `coverage` + `status`
(complete/partial/unknown). Pipeline tidak mempromosikan partial data tanpa tanda.

## Limitasi yang diketahui

- **Camoufox + cookie + reply expansion** = jalur terbaik
- CDP body-grab gagal pada body yang di-evict
- API comment butuh signature (msToken/X-Bogus)

## CI / Versioning Automation

CI jalan otomatis di setiap push / PR (`.github/workflows/ci.yml`):

| Gate | Cara verifikasi |
|---|---|
| build | `python -m py_compile` semua `.py` |
| import | cross-check 6 module + semua simbol |
| shell | `bash -n run.sh` **dan** `zsh -n run.sh` |
| tests | `run_dedup_quality_tests.py` (8) + `test_self_improvement.py` (11) |
| security | zero `LINKEDIN_PASSWORD`/`USERNAME` di kode |
| version | `.github/workflows/versioning.yml` – tag `v*` semver + scope policy |

**Versioning policy** (`docs/VERSIONING.md` + ponytail ladder):
* scope MVP v1 = **TikTok + LinkedIn**; platform baru ditambah *setelah* stabil
* semver; tag `v1.0.0` merepresentasikan "TikTok+LinkedIn stable"
* CI menerima *reject* otomatis jika provider baru ditambah sebelum v1.0 stabil

```bash
source .venv/bin/activate
python tests/run_dedup_quality_tests.py
python tests/test_self_improvement.py
bash -n run.sh && zsh -n run.sh          # cross-shell syntax
```

## License

MIT
