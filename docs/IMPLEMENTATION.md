# IMPLEMENTATION — chatgpt-response (16-point review) → social-data-engine

Ditulis 2026-08-16 setelah live test video @enxayeti/7669640839861112071.
Setiap poin review (`chatgpt-response`, file review 12KB) dipetakan ke modul + status + bukti observable.

## Arsitektur final

```
TikTok → collector.collect_video() → data/raw/ (L0)
       → pipeline.run_video()       → normalized/deduped/enriched/curated (L1-L3)
       → linkedin_consumer / tiktok_linkedin.py → LinkedIn matches/connect
Output: data/{raw,normalized,enriched,curated,rejected,manifests}
```

## Mapping 16 poin → status

| # | Poin review (chatgpt-response) | Modul | Status | Bukti |
|---|---|---|---|---|
| 1 | Pisah Collector/Processor | `collector.py` + `pipeline/legacy.py` | ✅ | `run_pipeline` delegate; consumer baca curated |
| 2 | Jangan simpan objek "final", raw+provenance | `tiktok_schema.py::RawComment` | ✅ | raw keys penuh: schema_version, source, video_id, comment_id, parent_comment_id, author_*, text_raw, capture_method, captured_at, collector_version, video_context |
| 3 | Schema versioning raw.v1 … curated.v1 | `tiktok_schema.py` | ✅ | `schema_version` = "raw.v1.0"/"normalized.v1.0"/"enriched.v1.0"/"curated.v1.0" |
| 4 | Dedup 3-level (exact/normalized/near) | `pipeline/legacy.py::stage_dedup` | ✅ | hash_exact/hash_normalized/simhash+hamming≤2; live 20 komentar |
| 5 | Pisah raw comment & conversation unit | `parent_comment_id` semua level | ⚠️ | parent/reply tersimpan; thread builder (RAG) belum |
| 6 | Context snapshot (caption/hashtags/creator) | `video_context` raw/normalized | ✅ | live: caption "first day" tersimpan |
| 7 | Jangan LLM-enrich semua — gating | `quality_score` + `stage_quality_gate` | ✅ | heuristic dulu, LLM annotation P2 |
| 8 | Quality score multi-dimensi | `pipeline/legacy.py::quality_score` | ✅ | semantic_density, spam_probability, toxicity, curated_score; fix: >1 URL → spam 0.95 |
| 9 | Data lineage | `provenance` enriched + collector/normalizer version | ✅ | "tiktok-scrapper@0.4.0", "pipeline@1.0.0" |
| 10 | Parquet + JSONL → JSONL dulu (YAGNI) | `write_jsonl` semua stage | ✅ | JSONL only |
| 11 | Dataset manifest | `generate_manifest` | ✅ | live: manifests/2026-08-16/…manifest.json |
| 12 | Pisah TikTok↔LinkedIn (consumer) | `linkedin_consumer.py` + `tiktok_linkedin.py` | ✅ | monolith 1128→367 baris |
| 13 | Annotation + verification | `stage_enrich` + `verify_names` | ⚠️ | verify di consumer; LLM annotation butuh 9Router |
| 14 | Job/checkpoint system | `collector.py::save_job/load_job` | ✅ | state/jobs/<vid>.json; resume |
| 15 | Output jangan langsung ke memory | `data/curated/` + manifest | ✅ | curated corpus terpisah; RAG via Orama P2 |
| 16 | Prioritaskan refactor boundary | seluruh refactor | ✅ | P0/P1 selesai; P2/P3 didefer |

## Bukti live (2026-08-16, @enxayeti)

```
collector: iter 1: dom=+20 cdp=+0 total=20; Done. 20 raw comments
pipeline : normalize 20 → dedup kept=20 → enrich 20 → quality curated=20 → manifest
curated  : @viergod 'let’s connect guyss | Kadaffi'
           @xcelynnz "let's connect guys| Sherlyn Novtrisya…"
           @no.late  "let's connect | Fitri Nurul Fatimah mahasiswi HI UMY"
           @deloktp  'mauu connect dong kak'
```

## Bug difix lewat live test

1. `RawComment.to_dict()` double-pop → KeyError (pop 2×)
2. `comment_id` dari `data-e2e="comment-level-N"` (index, duplikat) → dedup salah buang 19/20; fix hash `dom_*`
3. nodriver `evaluate()` membungkus hasil JS jadi list → crash; normalize
4. `Author` flat/dataclass mismatch → handle hilang di normalize; rekonstruksi Author
5. `_capture_pass` defined-setelah-call → UnboundLocalError; pindah ke atas
6. Nav retry + block detection + View-all + wheel scroll (restorasi monolith)
7. `--force` flag pipeline untuk re-process schema update

## Deferred (P2/P3, YAGNI)

- Parquet output (#10) — JSONL cukup, tambah saat corpus besar
- LLM annotation/identity (#13) — butuh 9Router; `extract_identities` siap
- Thread/conversation-unit builder (#5) — RAG nanti
- Orama/Mark integration (#15) — curated siap di-embed

Lihat juga:
- `chatgpt-response.md` — design brief (visi Social Data Engine)
- `VERIFICATION.md` — cross-check kode vs klaim 16-poin (build/test matrix)