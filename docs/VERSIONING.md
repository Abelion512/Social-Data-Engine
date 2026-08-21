# Versioning — MVP v1 (Semantic)

> **MVP v1 = TikTok + LinkedIn saja.** Platform baru ditambah **hanya setelah
> kombinasi TikTok → LinkedIn stabil** pada kriteria di bawah.

---

## Versi (semantic)

| Versi | Scope | Stabil? | Keterangan |
|---|---|---|---|
| `v0.x` | eksplorasi / prototype TikTok scraper | ❌ belum | baseline awal |
| **`1.0.0` (MVP v1)** | TikTok → LinkedIn saja | **ya** (lihat kriteria) | semua data platform dinaik ke `raw.v1 → curated.v1` |
| `1.x.0` | + provider baru (YouTube/Reddit) | ⏳ — ditambah **setelah** TikTok+LinkedIn stabil |
| `2.0.0` | schema breaking change | — |
| `1.0.x` | patch / bug / self-improvement rule tuning | ya | tidak breaking |

### Semantic versioning rules (project)

```
MAJOR  = schema breaking  (raw.v1 → raw.v2)        → reset semua data
MINOR  = fitur baru yang konsisten dengan schema  (v1.x)  → append-safe
PATCH  = bug fix / perf / self-improvement tuning (v1.0.x) → idempotent re-runnable
```

`COLLECTOR_VERSION` dan `SCHEMA_VERSION` di `src/tiktok_schema.py` serta
`PIPELINE_VERSION` di `src/pipeline/improve.py` / `mapper.py` dinaik per-MINOR
ketika logika berubah; per-MAJOR bila field dihapus/diganti nama.

---

## Kriteria "stabil" (MVP v1)

Dikaitkan ke poin 2 user: *"stabil = architecture bisa scrape isi video & caption,
semua reply bertingkat, photo, sticker berhasil di-normalize"*.

| # | Kriteria | Status kode | Evidence |
|---|---|---|---|
| 1 | **Photo URL support** (`/photo/ID`) | ✅ | `collector.py` `re.search(r"/(?:video\|photo)/(\d+)", url)` |
| 2 | **Video URL support** (`/video/ID`) | ✅ | regex sama di atas |
| 3 | **Isi video + caption** | ✅ | `extract_video_context()` → `caption`, `hashtags`, `creator`, `create_time` |
| 4 | **Reply bertingkat (threading)** | ✅ | `RawComment.parent_comment_id` + recursive expand (max 5 ronde) di collector |
| 5 | **Photo / sticker / media di comment** | ✅ | `RawComment.images: List[str]` + `tiktok_to_canonical` simpan di `content.metadata.images` |
| 6 | **Normalize** | ✅ | `src/schema/mapper.py::tiktok_to_canonical` + `tiktok_schema.normalize_text` |
| 7 | **Raw + normalized + enriched + curated versioning** | ✅ | `raw.v1` / `normalized.v1` / `enriched.v1` / `curated.v1` + canonical `observation.v1` |
| 8 | **Quality gate** | ✅ | `src/pipeline/quality.py` — `compute_quality_score`, `passes_gate` |
| 9 | **Multi-tier dedup** | ✅ | exact → normalized → near-duplicate (Jaccard bigram 0.85) |
| 10 | **Idempotent pipeline + resume** | ✅ | `src/pipeline/stages.py::StageRunner` (manifest-based skip + resume) |
| 11 | **Cross-platform identity resolution** | ✅ | `src/pipeline/identity.py` (confidence + evidence) |
| 12 | **MARK export + manifest** | ✅ | `src/export/mark.py`, `src/export/manifest.py` |
| 13 | **Auto/Recursive Self-Improvement** | ✅ | `src/pipeline/improve.py` — observe→plan→act→evaluate loop |
| 14 | **Cookie-based auth (no password)** | ✅ | `run.sh` (zsh-safe); `login_only()` manual browser; tidak ada password di kode |

**Stabil = semua ✅ di atas + test suite pass.**

---

## Test suite (MVP v1)

| Suite | Perintah | Hasil target |
|---|---|---|
| Dedup + quality | `python tests/run_dedup_quality_tests.py` | 8 passed |
| Self-improvement | `python tests/test_self_improvement.py` | 11 passed |
| Pytest (optional) | `python -m pytest tests/ -v` | semua pass |

```bash
source .venv/bin/activate
python tests/run_dedup_quality_tests.py
python tests/test_self_improvement.py
```

---

## Rekomen: pre-release checklist v1.0.0

✅ Live test 1 video TikTok **photo** (`/photo/ID`) — 72 raw, 39 curated collected; quality mean=0.958 (`/photo/ID`) + 1 **video** (`/video/ID`)
⚠️ Verify reply threaded — reply-parent parsing patched (parse `?comment_id=` URL query); butuh desktop verify (server kill proses) berada di JSONL curated dengan
      `parent_comment_id` yang benar
✅ Verify `images`/`sticker` schema + extractor ada — komentar ini text-only (kosong); populated bila komentar ada media URL tertangkap di comment
- [ ] Verify normalize: `text_raw` + `text_normalized` coexist
- [ ] `bash -n run.sh` + `zsh -n run.sh` — syntax OK
- [ ] `python tests/run_dedup_quality_tests.py && python tests/test_self_improvement.py`
- [ ] Semua kriteria 1–14 di atas ✅
- [ ] Tag: `git tag -a v1.0.0 -m "MVP v1: TikTok+LinkedIn stable"`

## Versioning Automation

Bump otomatis **major / minor / patch** via `scripts/version_bump.py`
(deterministic, stdlib-only, dry-run by default — ponytail ladder).

| Constants di-update | Lokasi | Authoritative |
|---|---|---|
| `PIPELINE_VERSION = "X.Y.Z"` | `src/schema/mapper.py:23` | ✅ semver master |
| `SCHEMA_VERSION = "X.Y"` | `src/tiktok_schema.py:20` | ✅ data layer |
| `COLLECTOR_VERSION = "X.Y.Z"` | `src/tiktok_schema.py:21` | ✅ mirrors pipeline |

```bash
python scripts/version_bump.py --bump minor          # preview (dry-run)
python scripts/version_bump.py --bump patch --commit   # commit + tag vX.Y.Z
python scripts/version_bump.py --bump minor --commit --push  # push tag
```

### Menentukan bump: `scripts/suggest_bump.py`

Bukan feeling — lihat commit sejak tag terakhir, klasifikasikan conventional
commits, ambil sinyal tertinggi:

| Commit | Sinyal |
|---|---|
| `BREAKING CHANGE:` footer atau `type!:` | major |
| `feat:` | minor |
| `fix:` / `perf:` | patch |
| lainnya (`chore:`/`docs:`/`test:`/`ci:`/…) | tanpa sinyal (floor patch) |

```bash
python scripts/suggest_bump.py            # laporan teks + saran
python scripts/suggest_bump.py --json     # machine-readable (untuk CI)
python scripts/suggest_bump.py --from vX.Y.Z   # override base tag
```

Saran = sinyal tertinggi yang ada. Contoh: satu `feat:` + tiga `fix:` →
**minor**. Script ini hanya *saran* — gerbang live-test (agents.md) tetap
wajib lolos sebelum bump nyata.

## Versioning Automation (CI)

`.github/workflows/versioning.yml`:

| Job | Pemicu | Perilaku |
|---|---|---|
| **`suggest-bump`** | setiap `pull_request` | laporan saran bump dari commits sejak tag terakhir — *informational*, tidak block merge |
| **`versioning-check`** | workflow_dispatch `action=check` | validasi manual: konstanta versi + compile + scope docs + JSON saran bump |
| **`auto-version-bump`** | workflow_dispatch `action=bump`, `bump=patch\|minor\|major` | downgrade guard → bump + commit + tag + push |
| `verify-tag` / `release-notes` / `prevent-new-platform-before-stable` | push tag `v*` / release | validasi semver, changelog otomatis, guard scope platform |

> **Downgrade guard** (di `auto-version-bump`): bila part yang dipilih lebih
> rendah dari saran `suggest_bump.py` (misal pilih `patch` padahal ada
> `feat:`), job gagal sebelum commit/tag. Memilih sama atau lebih tinggi lolos.
>
> 9Router / LLM model versions **never** dipush oleh tool ini — hanya data-layer constants.
