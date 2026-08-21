# Auto / Recursive Self-Improvement Architecture

> **Bukan model improvement** — ini adalah *architectural feedback loop*.
> Sistem mengamati hasil *collection / pipeline*-nya sendiri, mendeteksi
> regresi/stall, merencanakan aksi remediatif yang **deterministik**, menerapkannya,
> dan **merekrut** sampai hasil stabil atau anggaran iterasi habis.

---

## Prinsip

1. **Observable** — setiap run (per video) menghasilkan `PipelineMetrics`.
2. **Planner bersifat deterministik** — rule engine, tidak memakai LLM apapun. Dapat
   diuji tanpa jaringan/browser (*see tests/test_self_improvement.py*).
3. **Executor terpisah** — logika aksi (browser baru, batch kecil, ganti provider)
   dipisahkan dari Planner agar bisa diganti / dimock.
4. **Auditable** — setiap iterasi dicatat ke
   `data/manifests/<video_id>.improve.jsonl` (provenance-aware improvement).
5. **Terminasi pasti** — loop dibatasi `max_iter`; tidak rekursif tak terbatas.

---

## Arsitektur

```
┌──────────────────────────────────────────────────────────────────────┐
│  observe                                                             │
│  ┌──────────────┐  metrics   ┌────────────┐  plan(Actions)  ┌──────────┐│
│  │ Observer     │───read────▶│ Planner    │───────────────▶│ Actions  ││
│  │ (Pipeline    │            │ (rules +   │                 │(retry,   ││
│  │  Metrics)    │◀──re-measure│ thresholds)│◀──── act ────── │ split,   ││
│  └──────────────┘             └─────┬──────┘                  │ switch)││
│                                     │ execute                  └──┬───┘│
│                          ┌────────────────────┐                  │     │
│                          │ Executor           │◀─────────────────┘     │
│                          │ (re-collect w/      │  re-run collect+      │
│                          │  overridden params) │  process, re-observe  │
│                          └─────────┬──────────┘                     │
│                                    │                                │
│                          ┌─────────▼──────────┐                      │
│                          │ Evaluator          │                      │
│                          │ (is_stable /       │                      │
│                          │  is_improving)     │                      │
│                          └─────────┬──────────┘                      │
│                          ya │   stop / done                         │
│                          tidak│  │ loop ≤ max_iter                 │
└─────────────────────────────────┴──┴───────────────────────────────┘
```

---

## Komponen (`src/pipeline/improve.py`)

| Simbol | Peran |
|---|---|
| `PipelineMetrics` | Snapshot observability: `reported`, `captured`, `coverage`, `avg_quality`, `dup_rate`, `partial`, `stall_reason` |
| `Action` (enum) | `RETRY_BROWSER`, `SPLIT_BATCH`, `SWITCH_PROVIDER`, `EXPAND_REPLIES`, `RECOVER_PARTIAL`, `ADJUST_GATE`, `REQUEUE`, `NO_CHANGE` |
| `ImprovementPlan` | Data hasil plan: `video_id`, `iteration`, `actions`, `rationale` |
| `ImprovementPlanner` | **Rule engine deterministik** — `plan()`, `is_stable()`, `is_improving()` |
| `SelfHealingPipeline` | **Orchestrator rekursif** — `run(video_id, url)` loop observe→plan→act→re-collect→evaluate |

---

## Stabilisasi kriteria (MVP v1)

Sesuai poin 2 user — "stabil" = data isi video & caption, **semua reply bertingkat,
photo, sticker berhasil di-normalize**:

| KPI | Target |
|---|---|
| `coverage` (captured / reported) | **≥ 0.95** |
| `partial` | **False** (tidak ada stall/incomplete) |
| `avg_quality` (curated_score) | **≥ 0.35** |

Jika metric stabil → loop berhenti (idempoten, hemat resource).

---

## Rule engine — decision matrix

| Kondisi | Aksi |
|---|---|
| `partial` atau `coverage < 0.95` + `stall_reason` mengandung `EPIPE`/`crash`/`browser` | `RETRY_BROWSER` + `SPLIT_BATCH` |
| `partial` atau `coverage < 0.95` + tidak crash | `RECOVER_PARTIAL` + `EXPAND_REPLIES` + `SPLIT_BATCH` |
| `avg_quality < 0.35` | `ADJUST_GATE` (promosikan threshold + re-normalize) |
| `dup_rate > 0.20` | *cattat saja* — duplikat bukan bug collector (data characteristic) |
| semua stabil, tidak ada regresi | `NO_CHANGE` |

---

## Executor — mapping Action → collector override

| Action | Override ke collector |
|---|---|
| `RETRY_BROWSER` | `fresh_browser=True` |
| `SPLIT_BATCH` | `scrolls = scrolls // 2` |
| `SWITCH_PROVIDER` | `provider="camoufox"` (swap driver) |
| `EXPAND_REPLIES` | `reply_depth = prev + 2` (max 7) |
| `RECOVER_PARTIAL` | `retry_partial=True` |
| `ADJUST_GATE` | `adjust_gate=True` (re-normalize quality) |

> Executor (`_apply`) bersifat **pluggable** — implementasi default di sini;
> provider-specific override bisa ditambahkan nanti via subclass.

---

## Penggunaan

```python
import asyncio
from pathlib import Path
from src.pipeline.improve import SelfHealingPipeline
from src.collector import collect_comments    # atau TikTokAdapter.collect
from src.pipeline.stages import StageRunner
from src.pipeline.quality import passes_gate

async def run_with_self_healing(video_id, url, base_dir="."):
    async def collector(vid, u, **overrides):
        raw = await collect_comments(u, video_id=vid, **overrides)
        return [tiktok_to_canonical(r) for r in raw]

    def processor(observations, base_dir, **kw):
        # normalize → dedup → quality gate → metrics
        ... (StageRunner + compute_quality_score) ...
        return metrics

    runner = SelfHealingPipeline(
        base_dir=Path(base_dir),
        collector=collector,
        processor=processor,
        max_iter=3,
    )
    return runner.run(video_id, url)
```

---

## Provenance

Setiap iterasi dicatat ke `data/manifests/<video_id>.improve.jsonl`:

```jsonl
{"iteration": 1, "actions": ["recover_partial","expand_replies","split_batch"],
 "rationale": "coverage 40% < target 95% → re-collect partial + reply expand",
 "metrics": {...}, "timestamp": "..."}
```

Keputusan improvement **selalu bisa dilacak balik** — tidak ada "black box".
