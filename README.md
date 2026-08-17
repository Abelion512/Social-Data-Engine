# TikTok Data Acquisition Engine

Menangkap komentar TikTok → pipeline data ber-lapis → consumer LinkedIn / RAG (Mark).

## Struktur

```
src/
├── tiktok_schema.py      # Schema raw/normalized/enriched/curated + provenance
├── collector.py          # Akuisisi: DOM/CDP capture, checkpoint, resume
├── pipeline.py           # normalize → dedup (3-level) → enrich → quality → manifest
├── linkedin_consumer.py  # Consumer LinkedIn (baca curated → match/connect)
└── tiktok_linkedin.py    # Entrypoint CLI: chain penuh TikTok → LinkedIn
data/                     # ignored — output pipeline (raw/normalized/enriched/curated/manifests)
state/                    # ignored — job checkpoint + hasil LinkedIn (CSV)
docs/                     # chatgpt-response (review), IMPLEMENTATION.md, GOAL-EVIDENCE.md
```

## Usage

```bash
# Login TikTok sekali (browser profil persist)
python src/collector.py --login

# Koleksi satu video
python src/collector.py "https://www.tiktok.com/@user/video/123" --max 300

# Pipeline penuh raw → curated
python src/pipeline.py --video 123

# Chain penuh TikTok → LinkedIn
python src/tiktok_linkedin.py "https://www.tiktok.com/@user/video/123" --max 100

# Auto-connect (butuh approval)
python src/tiktok_linkedin.py "https://..." --connect
```

## Model (9Router)

`.env` (ignored): `BASE_URL`, `API_KEY`, `MODEL_PLANNER`, `MODEL_VISION_*`.
Chain enrichment: Gemini (cepat) → planner → fallback → claude-work.

## Collection completeness

Collector melaporkan `reported` vs `captured` + `coverage` + `status`
(complete/partial/unknown). Pipeline tidak mempromosikan partial data tanpa
tanda. Lihat `docs/IMPLEMENTATION.md` untuk detail.

## Limitasi yang diketahui

- TikTok 2026 membatasi render komentar: hanya batch awal (mis. 20 dari 55)
  termuat tanpa interaksi level-OS yang disimulasikan penuh.
- CDP body-grab (`get_response_body`) gagal pada body yang di-evict
  (-32000); Fetch intercept kena race di nodriver — didokumentasikan sebagai
  P1. Playwright adalah jalur yang menjanjikan (response API bisa dibaca),
  eksperimen interaksi berlanjut.
- API comment langsung butuh signature (msToken/X-Bogus).
