# Agent Operating Rules

> **Golden rule: *always live-test before merge.***
> Tiap perubahan — regression, fitur baru, atau versioning bump — **harus**
> melewati live test (actual collection dari TikTok) **sebelum** masuk `main`.
> Unit test deterministik (std lib, mock) wajib hijau, **tapi tidak cukup**;
> live test adalah gerbang final keamanan & correctness.

Dokumen ini adalah **peraturan operasional agent** (bisa di-load oleh Claude Code /
Cursor / Codex — semua yang membaca `CLAUDE.md` konvensi). Semua PR ke repo ini
harus melekatkan checklist ini.

---

## 📜 Rule set

1. **Live-test before merge.** Tidak boleh merge kecuali satu session live
   collection berhasil dijalankan dan lolos stabilize gate (lihat §3).
2. **Deterministic planner.** `ImprovementPlanner` 100 % rule-based — tidak boleh
   ada LLM di loop perencanaan (`improve.py`).
3. **Dry-run by default.** `scripts/version_bump.py` tidak menulis sampai
   `--commit --push` eksplisit.
4. **Zero plaintext credentials.** Tidak pernah `LINKEDIN_PASSWORD`/`USERNAME`
   di kode; auth cookie-based saja (`run.sh` zsh+bash safe, `load_env()`).
5. **Ponytail ladder.** Berhenti di rung pertama yang berlaku: (1) YAGNI, (2)
   reuse yang sudah ada di repo, (3) stdlib, (4) fitur native platform, (5)
   dependency terpasang, (6) one-liner, (7) minimal yang jalan. *Never cut*:
   validation, error-handling, security, provenance, dan apa pun yang diminta
   user. Simplifikasi sengaja → komentar `ponytail:` (ceiling + upgrade path) +
   ledger `docs/PONYTAIL.md`. Bug fix = root cause, sekali di fungsi bersama.
6. **Provenance-wajib.** `PipelineMetrics` + tiap iterasi improvement dicatat ke
   `data/manifests/<video_id>.improve.jsonl`.
7. **Terminasi pasti.** `SelfHealingPipeline` selalu berhenti (`max_iter` budget).

---

## ✅ Pre-merge checklist (dijalankan tiap PR)

| No | Gate | Perintah | Must |
|---|---|---|---|
| 1 | Compile | `python -m py_compile src/*.py src/*/*.py scripts/*.py tests/*.py` | ✅ |
| 2 | Import + symbol | cross-check 6 modul + simbol (lihat `ci.yml job`) | ✅ |
| 3 | Unit tests (mock) | semua suite deterministik: `for s in tests/test_*.py tests/run_*_tests.py; do python "$s"; done` (CI glob — sekarang 19 suite / 202 assertion) | ✅ |
| 4 | Shell syntax | `bash -n run.sh && zsh -n run.sh` | ✅ |
| 5 | Security | `grep -rn LINKEDIN_PASSWORD\|LINKEDIN_USERNAME src/` → 0 | ✅ |
| 6 | Ponytail | `docs/PONYTAIL.md` ladder · CI step 6 (`TODO/FIXME/XXX/HACK` di `src/`+`scripts/` = 0) · **setiap inline `ponytail:` marker harus punya baris di `docs/PONYTAIL.md §6`** (CI step 7) · stdlib-only new code | ✅ |
| 7 | Dependencies | `python scripts/check_dependencies.py` — import pihak-3 baru yang tidak ada di `requirements.txt` **memblokir merge** (CI step 8; AST-scan, opsi optional yang dikomentari di requirements tetap sah) | ✅ |
| 8 | **Live test** | lihat §3 | ✅ **REQUIRED** |

> **Satu perintah untuk gate 1–7** (CI = 8 langkah yang sama):
> `bash scripts/preflight.sh` — hijau semua = siap minta live test.

## 🤖 Agent quick-start (contribusi/patching tanpa tebak-tebakan)

1. **Baca dulu, baru edit:** `agents.md` (file ini) · `docs/AGENT-GUIDE.md` (peta repo + aturan) · `docs/PONYTAIL.md` (ladder + ledger utang) · `docs/CURRENT-STATE.md` (apa yang ada hari ini) · `docs/SDD.md §3` (invarian boundary — runtime tidak mengimpor providers, dsb.).
2. **Pipeline yang benar:** `src/pipeline/canonical_runner.py` adalah satu-satunya implementasi stage — `src/pipeline/legacy.py` hanya re-export shim. Jangan edit stage di dua tempat.
3. **Tulis gate, bukan harapan:** logika baru non-trivial menyisakan satu runnable check (suite assert di `tests/`, tanpa framework). Kegagalan = akar masalah, sekali di fungsi bersama.
4. **Sederhanakan = catat:** marker `ponytail:` inline butuh baris di ledger §6 — CI menolak kalau salah satu hilang. Hindari `TODO/FIXME` (CI gagal).
5. **Dep pihak-3:** tambahkan di `requirements.txt` (baris aktif, atau komentar `#   pkg>=ver → consumer` untuk tool opsional) — tanpa itu PR diblokir oleh dependency gate.
6. **Verifikasi:** `bash scripts/preflight.sh` → semua hijau → baru live test (§3) → commit.

---

## 🔴 Live Test Procedure (MVP v1)

Live test = **actual collection dari TikTok** lewat Camoufox + cookie session.

> **Prasyarat:** cookie login sudah dipersisten di `~/.tiktok-linkedin/chrome-profile/`
> (profile persisten). Jika belum, jalankan **login manual dulu**.

### Step 1 — Sambungkan browser (human-in-the-loop, safety first)

> **Safety for human:** agent **tidak pernah** mengambil alih login manual
> atau captcha. Kaman selalu terlihat / kamu klik **Allow**.

Pilih satu (workflow normal kamu):

**A. Claude Code (desktop) — CDP connect ke Chrome/Brave yang tengah jalan**
`browser_selector.py` detect otomatis instance Chrome/Brave dengan
`--remote-debugging-port`; Claude Code buka URL, muncul dialog **“Allow”** →
kamu klik. Session persisten via profile yang sama.

```bash
source .venv/bin/activate
bash run.sh "<photo/video URL>" --max 50 --scrolls 40   # → browserSelector CDP
```

**B. Camoufox *visible* (anti-detect Firefox, terlihat jelas)**
Jika CDP tak connectable, launcher pakai Camoufox `headless=False` — Firefox
terbuka **terlihat** (bukan tersembunyi) supaya kamu bisa monitoring / login
manual kalau perlu.

```bash
bash run.sh "<URL>"                # mode —login → browser terbuka, login manual
```

Session persisten di `~/.tiktok-linkedin/chrome-profile/`.

> ⚠️ Di *headless server* (tanpa display + Firefox binary belum ter-cache),
> live collection **terbatas** — pakailah desktop Claude Code / Chrome CDP
> sebagaimana biasa. (Live-test di server hanya sampai browser-boot; semua
> crash kode sudah diperbaiki dan diverified di `VERIFICATION.md §7`.)

### Step 2 — Live collect dua URL (photo + video)
```bash
# setelah Step 1 (allow/login) selesai:
bash run.sh "https://www.tiktok.com/@coretanmalam2000/photo/7673343206544706837" --max 50 --scrolls 40
bash run.sh "https://www.tiktok.com/@enxayeti/video/7669640839861112071"      --max 50 --scrolls 40
# atau langsung (Claude Code desktop, -u agar output real-time):
python -u src/tiktok_linkedin.py "<URL>" --max 50 --scrolls 40
```

### Step 3 — Verify stabilize criteria (MVP v1)
Setelah tiap URL, cek output di `data/{curated,normalized,raw}/<YYYY-MM-DD>/<video_id>.jsonl`:

| Kriteria stabil | Cara verifikasi | Pass |
|---|---|---|
| **Photo + Video URL** support | regex `/(?:video\|photo)/(\d+)` parse video_id ✅ | ✅ |
| **Isi + caption** | `extract_video_context()` → `caption`, `hashtags`, `creator`, `create_time` ada | ✅ |
| **Reply bertingkat** | semua komentar punya `parent_comment_id`; recursive expand 5 ronde | ✅ |
| **Photo / sticker media** | `RawComment.images` → `content.metadata.images` tidak kosong | ✅ |
| **Normalize** | `text_raw` + `text_normalized` coexist di curated | ✅ |
| **Coverage ≥ 95 %** | `captured / reported >= 0.95` | ✅ |
| **Quality gate** | `avg_quality >= 0.35` (lihat `quality.py`) | ✅ |

### Step 4 — Self-healing loop
Jika coverage < 95 % atau `partial=True`, jalankan:
```bash
python -c '
from src.pipeline.improve import SelfHealingPipeline, ImprovementPlanner
from src.tiktok_linkedin import run_pipeline
# SelfHealingPipeline.run(video_id, url) → observe→plan→act→re-collect→evaluate
'
```
Loop auto-retry hingga stabil **atau** `max_iter=3` budget habis.

### Step 5 — Rekam hasil
Catat `video_id` + `final_coverage` + `iterations` ke `docs/VERIFICATION.md §5`
atau kartu test case.

---

## 📦 Versioning (dry-run dulu!)
```
python scripts/version_bump.py --bump minor        # preview, tak menulis
python scripts/version_bump.py --bump patch --commit --push   # baru commit + tag vX.Y.Z
```
> Jangan pernah `--commit` sebelum live test lewat. Versi bump = *after* stabil
> terbukti di live environment.

---

## 🧭 Reference dokumen
- `docs/VERIFICATION.md` — poin 1–6 build/test matrix + bug-fix record
- `docs/VERSIONING.md` — semver policy + MVP v1 kriteria stabil
- `docs/PONYTAIL.md` — ladder, tags, `ponytail:` ceiling convention, debt ledger
- `docs/SELF-IMPROVEMENT.md` — arsitektur RSI
- `docs/chatgpt-response.md` — design brief (visi penuh)

## ⚠ Live-test status
*Status terakhir: Live test **run #2 berhasil** — 44 curated, 7 threaded replies, quality 0.992 mean ✅ (coverage 22% guest; full ≥95% butuh login desktop).*


## ⚠ Live-test status (agent-tool-first, run #4)

- **Prinsip:** agent automation, bukan API batch. Registry rute URL→provider;
  `BrowserAgent` pilih `browser_read/click/scroll/expand_replies/route.capture/api_fetch/media_enrich`
  tools (manus.im extension style). Semua platform (TikTok/LinkedIn) → canonical
  `Observation` (saling komunikasi lewat satu schema).
- Local headless server **tidak bisa camoufox live** (CDP/display tertutup; proses `exit -1` >3min).
  Bukti sampai tuntas lewat:
  (a) `scripts/trace_comment.py --trace-all` → **✅ ALL 44 curated traced** (raw.id==curated.id,
      parent survives dedup, quality≥0.35, exit 0);
  (b) `BrowserAgent` dry-run goal→observe→plan→act trace, no browser → no crash ✅.
- Full action-level trace butuh **desktop**: `python -u src/tiktok_linkedin.py <URL>`
  (camoufox **visible** by default; atau Claude Code CDP *Allow*). Human-in-loop mandatory.
- Env-gated headless fallback (`CAMOUFOX_HEADLESS=true .venv/bin/python src/collector.py --camoufox URL`)
  tetap berlaku untuk run-run pendek (scrolls ≤ 50).

### Pluggable providers
| provider | module | goal default |
|---|---|---|
| tiktok | src/providers/tiktok.py | collect_threaded_replies |
| linkedin | src/providers/linkedin.py | collect_all_comments (scrape hook pluggable) |
Tambah platform: subclass `AgentProvider`, override `toolkit()` (selectors) + `_scrape_comments`, register URL regex.
