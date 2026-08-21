# References — TikTok Scraping (Comment Collection)

> Catatan arsitektur: bukan dokumen "how-to" biasa. Ini *why this design* —
> supatu mengerti sebab semua keputusan anti-detect di `src/`.

## 1. Mengapa scrape via API (`comment/list/`) bukan DOM klik saja?

TikTok **tidak pernah kembalikan >~120 komentar per sesi anonim/anonymous** —
setelah itu naik **challenge overlay** (verify/captcha/slider) secara progresif
(per-IP/per-fingerprint/per-page). Ini **bukan bug code**, ini anti-bot server-side.

- **DOM scrape** (`data-e2e="comment-level-N"`): cepat tapi **blocked** begitu
  challenge muncul; `visible=0` walaupun route-interception masih dapat data.
- **API fetch** (`https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id=...&count=100&cursor=...`):
  dapat data mentah JSON termasuk `text`, `cid`, `user`, `digg_count`, `reply_comment_total`.
- **Route-intercept**: tiangka listener `page.route("**/*")` tangkep response API
  secara *passive* (lazy-load saat scroll) → **fallback & kecepatan tinggi**.

### Strategi kami: **API-first + DOM-enrichment**
1. `fetch_comments_api(video_id, cursor)` — proactive pagination (`count=100` per
   page) sampai `has_more=false` atau `max_comments` tercapai. Ini **jalan ke 2K**.
2. `route intercept` — tangkep API response yang *TikTok* fetch otomatis (lazy),
   termasuk `/comment/list/reply/?comment_id=<PARENT_CID>` untuk **nested replies**.
3. DOM scrape (`human_click` reply-expand) — hanya sebagai *enrichment* sumber
   `images`/`audio`(voice)/`sticker` yang API tidak kirim.

## 2. Pagination (cursor) — pola mirip Apify `apify/tiktok-scraper`

Apify `tiktok-scraper` memakai pola **cursor-based pagination**:

```
GET https://www.tiktok.com/api/comment/list/
  ?aid=1988
  &aweme_id=<VIDEO_ID>        # = video_id (bukan URL slug)
  &count=20                    # Apify pakai 20, kami pakai 100 (lebih cepat ke 2K)
  &cursor=<PREVIOUS_CURSOR>    # << pagination token, BUKAN page_number
  &comment_style=2&from=web&device_platform=web
```

- `cursor` = token opaque dari response JSON; selalu gunakan `cursor` hasil
  response **terakhir** sebagai `cursor` query berikutnya. JANGAN reset ke 0
  sampai `has_more===false` (reset → loop duplicate → `seen_ids` skip → 0 new
  → stale-break). Kami persist `_capture_pass._api_cursor` & `_api_has_more`
  sebagai **function-attribute** agar survive antar scroll-iteration.
- `has_more` (1/0 di API) = sinyal berhenti. Jangan hentikan hanya karena
  satu page kosong (bisa lagi rate-limit) — **retry** (backoff 3-7s +
  `resolve_captcha_if_present`).

### Nested reply (thread)
- TikTok reply fetch terpisah: `GET .../comment/list/reply/?comment_id=<PARENT_CID>`.
- Response reply **tidak embed `parent_comment_id`** → parse dari query-param
  `comment_id=` di URL request, inject ke `RawComment.parent_comment_id`
  (lihat `collector._on_route` + `raw_from_api(parent_comment_id=...)`).
- Depth = `comment-level-N` di DOM, atau `parent_comment_id` chain di API.
  Kami flatten ke CSV `depth` (0=top, 1=reply) + `parent_comment_id`.

## 3. Assets (sticker / photo / voice note)

| Asset     | Di API (`comment` obj)         | Di DOM (`comment-level-N`)              |
|-----------|--------------------------------|------------------------------------------|
| Photo     | `images[]` (jarang)            | `<img src=…>` (tiktokcdn)                |
| Sticker   | tidak ada field khusus         | `<img src=…gif>` (sticker GIF)           |
| Voice     | tidak ada                      | `<audio src=…>` (voice note)             |

- Kami parse **DOM** (`DOM_SCRAPE_JS`) untuk `images`, `audio`, `sticker`
  (realtime, lazy-load) + `IMAGE_ENRICH_JS` post-scroll re-scrape.
- Schema `RawComment` → `images: List[str]`, `audio: List[str]`,
  `sticker: Optional[str]` — semua export ke CSV kolom terpisah.

## 4. Anti-detect (browsers bebas / camoufox / CDP)

- **CDP (primary)**: attach ke browser user yang sudah login
  (`connect_over_cdp`, **never launch** user browser, **never close**).
  Fingerprint natural + history login = **deteksi terendah**.
- **Camoufox (fallback)**: Firefox-based anti-detect. Inject cookie via
  `_camoufox_inject_cookies(cm)`.
- **`human.py`**: `human_move_mouse` (jittered trajektori), `human_click`
  (realistic offset click, bukan `.click()` polos), `human_scroll` (natural),
  `apply_stealth` (navigator.webdriver/languages spoof),
  `resolve_captcha_if_present` + `solve_slider_captcha` (vision via 9Router
  Gemini `VISION_MODEL`).

> Catatan: `browser.close()` di CDP attach = **terminate browser user** (PW 1.60
> tidak punya `Browser.disconnect`). Kita pakai `getattr(browser,"disconnect")`
> guarded + *never* `.close()` — lihat `_read_tiktok_session`/`_inject_cookies_and_recheck`
> `finally`. Ini sebab semua "browser tiba-tiba mati" bug.

## 5. Quick start (user-facing)
```bash
# 1. browser: buka dengan flag WAJIB (atau gunakan yg sudah running di 9222)
./scripts/open_browser.sh 9222          # brave/chrome --remote-debugging-port=9222

# 2. scrape 198 / 2K + CSV (anti-detect via browser user)
python -m src.collector "<TIKTOK_URL>" --csv --max 2000
#    atau via benchmark:
SDE_DATA_DIR="$(pwd)/data" python scripts/benchmark.py --urls "<URL>" --max-comments 2000 --csv

# 3. kalau challenge slider muncul → resolve manual sekali di browser
#    (atau beri LLM_API/VISION_MODEL env supaya solve_slider_captcha otomatis)
```

## 6. Environment & paths portable
- `$SDE_DATA_DIR` → alatkan `data/` ke mana saja (konsep sama `.venv`).
- Cookie: `$TIKTOK_COOKIES` atau default `~/.tiktok-linkedin/tiktok-cookies.json`
  (Cookie-Editor export). `sessionid` expiry cek → hanya `sessionid` valid
  dihitung sebagai login (`uid_tt`/`sid_tt` = tracking cookie, false-positive).
```
