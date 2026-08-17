#!/usr/bin/env python3
"""
TikTok Data Acquisition Engine — Collector

Menggantikan capture_comments() yang ada di tiktok_linkedin.py.
Fokus: ambil komentar mentah → tulis JSONL ke data/raw/

Checkpoint/resume:
  state/jobs/<video_id>.json → cursor, comments_seen, comments_written, status

Usage:
  python collector.py <tiktok_url> [--scrolls N] [--max N] [--resume job_id]
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

# Pastikan root repo (parent dari src/) masuk sys.path — diperlukan saat
# dieksekusi sebagai `python src/collector.py` (cwd bukan otomatis sys.path)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import nodriver as uc
import base64

from src.tiktok_schema import (
    RawComment,
    Author,
    raw_from_api,
    raw_from_dom,
    write_jsonl,
    COLLECTOR_VERSION,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"
DATA_DIR = _ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MANIFEST_DIR = DATA_DIR / "manifests"
JOB_DIR = STATE_DIR / "jobs"


def ensure_dirs():
    for d in (PROFILE_DIR, STATE_DIR, RAW_DIR, MANIFEST_DIR, JOB_DIR):
        d.mkdir(parents=True, exist_ok=True)


def today_stamp() -> str:
    return time.strftime("%Y-%m-%d")


def raw_path(video_id: str) -> Path:
    return RAW_DIR / today_stamp() / f"{video_id}.jsonl"


# ── Job checkpoint ─────────────────────────────────────────────────────────────
def save_job(video_id: str, data: dict):
    ensure_dirs()
    p = JOB_DIR / f"{video_id}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_job(video_id: str) -> Optional[dict]:
    p = JOB_DIR / f"{video_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    return None


# ── Browser ───────────────────────────────────────────────────────────────────
async def init_browser(headless: bool = False):
    ensure_dirs()
    # Singleton lock tertinggal dari run crash → Chrome gagal start (buka-close).
    # Bersihkan sebelum start setiap kali.
    try:
        for f in PROFILE_DIR.glob("Singleton*"):
            f.unlink()
    except Exception:
        pass
    try:
        browser = await uc.start(headless=headless, user_data_dir=str(PROFILE_DIR))
    except Exception as e:
        # Linux umum: sandbox gagal (root/no_sandbox) → retry tanpa sandbox
        print(f"[collector] uc.start failed ({e}); retry with no_sandbox")
        browser = await uc.start(headless=headless, user_data_dir=str(PROFILE_DIR),
                                 no_sandbox=True)
    try:
        import nodriver.cdp.network as net
        import nodriver.cdp.runtime as runtime
        tab = browser.main_tab
        # Buffer besar: response body tidak boleh di-evict sebelum handler
        # membacanya (root cause -32000 "No resource with given identifier").
        try:
            await tab.send(net.enable(max_total_buffer_size=100 * 1024 * 1024,
                                      max_resource_buffer_size=50 * 1024 * 1024))
        except TypeError:
            await tab.send(net.enable())  # fallback: versi lama tanpa arg
        await tab.send(runtime.enable())
        print("[collector] CDP network monitoring enabled")
    except Exception as e:
        print(f"[collector] CDP enable failed: {e}")
    return browser


# ── Video context probe ─────────────────────────────────────────────────-------
VIDEO_CTX_CACHE: Dict[str, dict] = {}


async def extract_video_context(tab) -> dict:
    """Extract caption, hashtags, creator, create_time from page DOM + SSR."""
    try:
        ctx_js = r"""(() => {
            const get = (sel) => document.querySelector(sel);
            const captionEl = get('[data-e2e="browse-video-desc"]') || get('[data-e2e="browse-desc"]') || get('h1');
            const caption = captionEl ? captionEl.textContent.trim() : '';
            const hashtags = Array.from(document.querySelectorAll('a[href*="/tag/"]'))
                .map(a => a.textContent.trim()).filter(Boolean);
            const creatorEl = get('[data-e2e="browser-nickname"]') || get('a[href*="/@"]') || get('[data-e2e="avatar"]');
            const creator = creatorEl ? (creatorEl.textContent || creatorEl.getAttribute('href') || '').trim().replace(/^@/, '') : '';
            const canonical = document.querySelector('link[rel="canonical"]')?.href || '';
            const m = canonical.match(/\/(@[^/]+)\//) || window.location.href.match(/\/(@[^/]+)\//);
            const creatorHandle = m ? m[1].replace('@', '') : creator.split('/').pop();
            return { caption, hashtags: [...new Set(hashtags)], creator: creatorHandle, canonical, url: window.location.href };
        })()"""
        data = await tab.evaluate(ctx_js)
        # nodriver kadang membungkus hasil JS jadi list — normalisasi dulu
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            print(f"[collector] video context: unexpected shape {type(data)}")
            return {"video_id": "", "video_url": "", "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""}
        video_url = data.get("url", "")
        # Coba dapatkan video_id dari URL
        m = re.search(r"/(?:video|photo)/(\d+)", video_url)
        video_id = m.group(1) if m else ""
        return {
            "video_id": video_id,
            "video_url": video_url,
            "caption": data.get("caption", ""),
            "hashtags": data.get("hashtags", []),
            "creator": data.get("creator", ""),
            "create_time": int(time.time()),   # fallback, SSR biasanya ganti
            "transcription": ""
        }
    except Exception as e:
        print(f"[collector] video context error: {e}")
        return {"video_id": "", "video_url": "", "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""}


# ── DOM scraper (inline JS, sama seperti original) ─────────────────────────────
DOM_SCRAPE_JS = r"""(() => {
        const out = [];
        const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
        items.forEach(el => {
            const raw = el.textContent ? el.textContent.replace(/\s+/g, ' ').trim() : '';
            const imgs = Array.from(el.querySelectorAll('img[src*="tiktokcdn"], img[src^="http"]'))
                .map(i => i.src).filter(s => s && !s.includes('static/') && !s.includes('avatar')).slice(0, 4);
            if ((!raw || raw.length < 3) && imgs.length === 0) return;
            const wrapper = el.closest('[class*="DivCommentObjectWrapper"], [data-e2e^="comment-item-"]') || el.parentElement;
            let uname = '';
            if (wrapper) {
                const a = wrapper.querySelector('a[href*="/@"]');
                uname = a ? a.getAttribute('href').replace(/^\/?@/, '').split('?')[0] : '';
            }
            if (!uname) {
                const nu = document.querySelector('[data-e2e*="comment-username-"]');
                uname = nu ? nu.textContent.trim().replace(/^@/, '') : '';
            }
            let cid = el.getAttribute('data-e2e') || el.getAttribute('data-testid') || '';
            // TikTok pakai data-e2e="comment-level-N" (index, BUKAN unique id) —
            // semua elemen level sama → cid identik. Fallback ke hash raw+user.
            if (!cid || /^comment-level-\d+$/.test(cid)) {
                let h = 0;
                const s = (uname + '|' + raw).substring(0, 120);
                for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff;
                cid = 'dom_' + h.toString(36);
            }
            out.push({
                raw: raw.substring(0, 400),
                username: uname || raw.split(' ')[0],
                images: imgs,
                comment_id: cid,
            });
        });
        const seen = new Set();
        return JSON.stringify(out.filter(o => (seen.has(o.raw + '|' + o.images.join(',')) ? false : (seen.add(o.raw + '|' + o.images.join(',')), true))));
    })()"""

SCROLL_JS = r"""(() => {
        var cont = null;
        // 0) Simulasi interaksi: fokus panel + klik item terakhir (trigger SPA)
        var last = null;
        try {
            var all = document.querySelectorAll('[data-e2e^="comment-level-"]');
            if (all.length) { last = all[all.length - 1]; last.scrollIntoView({ block: 'end' }); last.focus(); }
        } catch (e) {}
        var w = document.querySelector('[class*="DivCommentMain"]');
        if (w) {
            w.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            w.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        }
        // 1) Cari container komentar: selector spesifik + DETEKSI GENERIK
        var sl = document.querySelector('[data-e2e="comment-list"]');
        if (sl) {
            var p = sl.parentElement;
            while (p && p.scrollHeight <= p.clientHeight + 10) p = p.parentElement;
            if (p) cont = p;
        }
        if (!cont) cont = document.querySelector('[class*="DivCommentListContainer"]');
        if (!cont) cont = document.querySelector('[class*="DivCommentMain"]');
        if (!cont) cont = document.querySelector('[class*="CommentListContainer"]');
        // Generik: elemen scrollable yang MENGANDUNG komentar (TikTok class hash berubah)
        if (!cont) {
            var cands = Array.from(document.querySelectorAll('div')).filter(e => {
                return e.scrollHeight > e.clientHeight + 50 &&
                       e.querySelectorAll('[data-e2e^="comment-level-"]').length > 0;
            }).sort((a, b) => b.scrollHeight - a.scrollHeight);
            if (cands.length) cont = cands[0];
        }
        // 2) Klik semua trigger load-more / expand reply
        document.querySelectorAll('[data-e2e*="load-more"], [class*="LoadMore"], [data-e2e*="comment-load"], [data-e2e*="view-more-"]').forEach(el => { try { el.click(); } catch (e) {} });
        // 3) Scroll kontainer (jika ada) + window (jika tidak)
        if (!cont) {
            window.scrollBy(0, 900);
            return 'window-scroll';
        }
        var items = cont.querySelectorAll('[data-e2e^="comment-level-"]');
        if (items.length) { items[items.length - 1].scrollIntoView({ behavior: 'instant', block: 'end' }); }
        cont.scrollTop = cont.scrollHeight;
        cont.dispatchEvent(new WheelEvent('wheel', { deltaY: 1200, bubbles: true, cancelable: true }));
        return cont.scrollHeight + ':' + cont.scrollTop;
    })()"""

CLICK_COMMENT_PANEL_JS = r"""(function() {
        var q = function(sel) { return document.querySelector(sel); };
        var icon = q('[data-e2e="comment-icon"]');
        if (icon) { var btn = icon.closest('button') || icon; btn.click(); return 'clicked data-e2e'; }
        var count = q('[data-e2e="comment-count"]');
        if (count) { var b = count.closest('button') || count; b.click(); return 'clicked comment-count'; }
        var buttons = document.querySelectorAll('button[aria-label]');
        for (var i = 0; i < buttons.length; i++) {
            var label = buttons[i].getAttribute('aria-label').toLowerCase();
            if (label.includes('comment') || label.includes('comentar')) {
                buttons[i].click(); return 'clicked aria-label';
            }
        }
        return 'not found';
    })()"""

VIEW_ALL_JS = r"""(() => {
    var els = document.querySelectorAll('div[role="button"], span, p, button');
    for (var i = 0; i < els.length; i++) {
        var t = (els[i].textContent || '').trim();
        if (/^View (all )?\d+ comments?$/i.test(t) || /^Lihat (semua )?\d+ komentar/i.test(t)) {
            els[i].click(); return 'clicked view-all: ' + t.slice(0, 40);
        }
    }
    return 'none';
})()"""

CHECK_BLOCK_JS = r"""(() => {
    var bt = document.body ? document.body.innerText.slice(0, 3000) : '';
    var u = window.location.href;
    return JSON.stringify({ url: u, blocked: /login|verify/i.test(u) || (/Verify/.test(bt) && /human/.test(bt)) });
})()"""


# ── CDP interceptor: Fetch (baca body FULL saat paused) ───────────────────────
# ResponseReceived + getResponseBody gagal (-32000: body evicted). Fetch
# intercept menangkap body di titik PAUSED (body pasti ada di memori).
async def setup_cdp_handler(tab):
    """Fetch.requestPaused → baca body komentar → continue request.

    Counter: observed → comment_like → parsed_ok → comments (poin #6).
    """
    import nodriver.cdp.fetch as f
    import nodriver.cdp.network as cdp_network
    captured_pages: list = []
    counters = {"observed": 0, "comment_like": 0, "parsed_ok": 0, "comments": 0}

    async def _on_paused(event: f.RequestPaused):
        url = event.request.url or ""
        if "comment" not in url and "aweme" not in url:
            try:
                await tab.send(f.continue_request(event.request_id))
            except Exception:
                pass
            return
        counters["observed"] += 1
        counters["comment_like"] += 1
        is_reply = "/reply/" in url or "comment/list/reply" in url
        # Body di titik paused — pasti tersedia
        try:
            body = await tab.send(f.get_response_body(event.request_id))
            body_str, is_b64 = body
            if is_b64:
                body_str = base64.b64decode(body_str).decode("utf-8", errors="replace")
            data = json.loads(body_str)
        except Exception as e:
            err = str(e).splitlines()[0] if str(e) else type(e).__name__
            if counters["parsed_ok"] == 0:
                print(f"[cdp] fetch body err ({url[-60:]}): {err[:100]}")
            await tab.send(f.continue_request(event.request_id))
            return
        await tab.send(f.continue_request(event.request_id))
        comments = data.get("comments") if isinstance(data, dict) else None
        if not isinstance(comments, list):
            return
        counters["parsed_ok"] += 1
        counters["comments"] += len(comments)
        page = {
            "comments": comments,
            "has_more": data.get("has_more", 0),
            "cursor": data.get("cursor", 0),
            "is_reply": is_reply,
        }
        captured_pages.append(page)
        label = "reply" if is_reply else "comment"
        print(f"[cdp] {label} page: +{len(page['comments'])}")

    # Enable SETELAH handler terdaftar — event yang masuk setelah enable
    # pasti punya domain aktif (tidak ada race -32000).
    try:
        await tab.send(f.enable())
        print("[collector] Fetch domain enabled (all responses)")
    except Exception as e:
        print(f"[collector] Fetch enable err: {e}")
        return [], counters

    tab.add_handler(f.RequestPaused, _on_paused)
    print("[collector] Fetch CDP handler registered")
    return captured_pages, counters


# ── Main capture loop ─────────────────────────────────────
def _b36(n: int) -> str:
    """JS-compatible toString(36) — cocok dengan hash DOM di DOM_SCRAPE_JS."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


async def _probe_reported_count(tab) -> int:
    """Poin #4: baca jumlah komentar yang dilaporkan TikTok UI.

    Prioritas: data-e2e="comment-count" → tombol dengan aria-label berisi
    "comment(s)" → teks "N comments". Tidak ada → 0 (=unknown).
    """
    js = r"""(() => {
        const q = (s) => document.querySelector(s);
        const el = q('[data-e2e="comment-count"]');
        if (el) {
            const t = (el.getAttribute('title') || el.textContent || '').trim();
            const m = t.match(/(\d[\d.,]*)/);
            if (m) return parseInt(m[1].replace(/[.,]/g, ''), 10) || 0;
        }
        const buttons = document.querySelectorAll('button[aria-label]');
        for (const b of buttons) {
            const l = (b.getAttribute('aria-label') || '').toLowerCase();
            const m = l.match(/(\d[\d.,]*)\s*comments?/);
            if (m) return parseInt(m[1].replace(/[.,]/g, ''), 10) || 0;
        }
        const bt = document.body ? document.body.innerText : '';
        const m = bt.match(/(\d[\d.,]*)\s*comments?/i);
        if (m) return parseInt(m[1].replace(/[.,]/g, ''), 10) || 0;
        return 0;
    })()"""
    try:
        r = await tab.evaluate(js)
        if isinstance(r, list):
            r = r[0] if r else 0
        return int(r or 0)
    except Exception:
        return 0


API_COMMENT_FETCH_JS = """async (cursor) => {
    const id = window.__ttc_video_id__ || '';
    const msT = Math.round(Date.now());
    const url = `https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id=${id}&count=50&cursor=${cursor || 0}&comment_style=2&from=web&device_platform=web&channel=normal&enter_from=comment_detail_page&current_region=ID&os=linux&sec_user_id&webcast_language=en&msToken=&X-Bogus=`;
    const r = await fetch(url, {
        headers: { 'accept': 'application/json, text/plain, */*', 'x-secsdk-csrf-token': '', 'sec-fetch-site': 'same-origin' },
        credentials: 'include'
    });
    const j = await r.json();
    return JSON.stringify({ comments: j.comments || [], has_more: j.has_more || 0, cursor: j.cursor || 0 });
}"""


# ── Jalur API langsung (via fetch di halaman, same-origin) ──
# CDP body-grab gagal (-32000: body di-evict). API fetch lewat JS di halaman
# memakai cookie session browser → dapat semua komentar ber-pagination.
async def fetch_comments_api(tab, video_id: str, cursor: int = 0) -> dict:
    """Fetch komentar dari TikTok API via evaluate (same-origin cookie).

    Catatan: tab.evaluate TIDAK menunggu Promise async IIFE — pakai
    synchronous XHR + polling, atau evaluate dengan awaitPromise explicit.
    """
    js = f"""async () => {{
        const url = `https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id={video_id}&count=50&cursor={cursor}&comment_style=2&from=web&device_platform=web&channel=normal&enter_from=comment_detail_page`;
        try {{
            const r = await fetch(url, {{ credentials: 'include', headers: {{ 'accept': 'application/json, text/plain, */*', 'sec-fetch-site': 'same-origin' }} }});
            const j = await r.json();
            return JSON.stringify({{ comments: j.comments || [], has_more: j.has_more || 0, cursor: j.cursor || 0 }});
        }} catch (e) {{
            return JSON.stringify({{ error: String(e) }});
        }}
    }}"""
    try:
        out = await tab.evaluate(js, await_promise=True)
    except Exception as e:
        return {"error": f"evaluate: {e}"}
    if isinstance(out, list):
        out = out[0] if out else None
    if isinstance(out, dict):  # nodriver bisa langsung parse
        return out
    if isinstance(out, str) and out.strip():
        try:
            return json.loads(out)
        except Exception:
            return {"error": f"parse: {out[:100]}"}
    return {"error": "empty result"}


async def collect_video(
    video_url: str,
    max_scrolls: int = 60,
    max_comments: int = 300,
    resume: Optional[str] = None,
) -> Dict:
    """
    Collector utama: ambil komentar dari satu video TikTok.
    Output: JSONL raw di data/raw/<date>/<video_id>.jsonl
    """
    ensure_dirs()

    # Parse video_id
    m = re.search(r"/(?:video|photo)/(\d+)", video_url)
    if not m:
        print(f"[!] Tidak bisa ekstrak video_id dari {video_url}")
        return {"error": "invalid_url", "video_url": video_url}
    video_id = m.group(1)
    print(f"[collector] Video ID: {video_id}")

    # Resume jika ada
    job_state = load_job(video_id) if resume else None
    if job_state:
        print(f"[collector] Resume job: cursor={job_state.get('cursor')}, seen={job_state.get('comments_seen')}")
    else:
        job_state = {"job_id": resume or f"job_{video_id}_{int(time.time())}", "video_id": video_id, "cursor": 0, "comments_seen": 0, "comments_written": 0, "status": "running"}
        job_state["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    browser = await init_browser()
    tab = browser.main_tab

    # Setup CDP
    cdp_pages, cdp_counters = await setup_cdp_handler(tab)

    # Navigate (dengan retry + deteksi blokir)
    for attempt in range(3):
        print(f"[collector] Navigating (attempt {attempt+1})...")
        await tab.get(video_url)
        await asyncio.sleep(6)

        status = await tab.evaluate(CHECK_BLOCK_JS)
        try:
            s = json.loads(status) if isinstance(status, str) else (status if isinstance(status, dict) else {})
        except Exception:
            s = {}
        if s.get("blocked"):
            print(f"[!] TikTok blocked (login/verify) at {s.get('url', '?')}")
            browser.stop()
            return {"error": "blocked", "status": "blocked"}
        if attempt < 2:
            await asyncio.sleep(3)  # biarkan SPA settle sebelum cek ulang

    # Extract video context ONCE (cached)
    video_ctx = await extract_video_context(tab)
    video_ctx["video_id"] = video_id
    video_ctx["video_url"] = video_url

    # Reported comment count dari UI (poin #4: bedakan reported vs captured)
    reported_count = await _probe_reported_count(tab)

    # ── Capture: maksimal 3 pass navigasi; berhenti bila sudah dapat komentar ──
    out_path = raw_path(video_id)
    all_raw: List[RawComment] = []
    seen_ids: set = set()

    async def _capture_pass(tab_, video_ctx_, cdp_pages_, all_raw_,
                            seen_ids_, out_path_, video_id_, job_state_,
                            max_scrolls_, max_comments_):
        """Satu pass capture: DOM + CDP + scroll.

        Stop condition: tidak ada ID unik baru selama STALE_LIMIT iterasi
        (bukan len(DOM)). Telemetri [COLLECT] per iter: visible/new/total.
        """
        new = 0
        stale = 0
        STALE_LIMIT = 6
        prev_total = len(all_raw_)
        for i in range(max_scrolls_):
            dom_added = 0
            try:
                dom_str = await tab_.evaluate(DOM_SCRAPE_JS)
                # nodriver membungkus hasil JS jadi list — normalisasi dulu
                if isinstance(dom_str, list):
                    dom_str = dom_str[0] if dom_str else None
                rows = json.loads(dom_str) if isinstance(dom_str, str) else (dom_str or [])
            except Exception as e:
                print(f"[collector] DOM scrape err: {e}")
                rows = []
            visible = len(rows)
            dom_before = len(all_raw_)

            for row in rows:
                cid = row.get("comment_id", "")
                if cid in seen_ids_:
                    continue
                seen_ids_.add(cid)
                r = raw_from_dom(row, video_ctx_)
                all_raw_.append(r)
                dom_added += 1

            # Drain CDP pages (identity preferensial: cid API nyata, bukan hash DOM)
            cdp_added = 0
            while cdp_pages_:
                page = cdp_pages_.pop(0)
                for api_comment in page.get("comments", []):
                    cid = api_comment.get("cid", "")
                    # Kunci identity lintas-path: jika komentar DOM sudah tertangkap
                    # (hash dom_*), tandai cid API-nya juga agar tidak dobel.
                    if cid:
                        if cid in seen_ids_:
                            continue
                        fprint = str(api_comment.get("text", ""))[:120] + "|" + str((api_comment.get("user", {}) or {}).get("unique_id", ""))
                        h = 0
                        for ch in fprint:
                            h = (h * 31 + ord(ch)) & 0x7fffffff
                        # base-36, sama dengan toString(36) di DOM_SCRAPE_JS
                        dom_alias = "dom_" + _b36(h)
                        if dom_alias in seen_ids_:
                            seen_ids_.add(cid)  # komentar sudah ada via DOM → tandai saja
                            continue
                        seen_ids_.add(cid)
                    r = raw_from_api(api_comment, video_ctx_, method="cdp")
                    all_raw_.append(r)
                    cdp_added += 1

            # ── Jalur API langsung (fetch same-origin di halaman) ──
            # Pelengkap ketika CDP body-grab gagal: paginasi cursor API.
            api_added = 0
            cursor = getattr(_capture_pass, "_api_cursor", 0)
            has_more = getattr(_capture_pass, "_api_has_more", True)
            if has_more:
                page = await fetch_comments_api(tab_, video_id_, cursor) or {}
                if page.get("error"):
                    print(f"[api] fetch err: {page['error'][:100]}")
                    has_more = False
                else:
                    for api_comment in page.get("comments", []):
                        cid = api_comment.get("cid", "")
                        if cid and cid in seen_ids_:
                            continue
                        if cid:
                            seen_ids_.add(cid)
                        r = raw_from_api(api_comment, video_ctx_, method="api")
                        all_raw_.append(r)
                        api_added += 1
                    _capture_pass._api_cursor = page.get("cursor", 0)
                    _capture_pass._api_has_more = bool(page.get("has_more", 0))
            # Reset setelah 1 panggilan API per iter — avoid infinite tanpa scroll
            if api_added == 0:
                _capture_pass._api_has_more = False
                _capture_pass._api_cursor = 0

            # Expand replies
            try:
                await tab_.evaluate("""(() => {
                    var clicked = 0;
                    document.querySelectorAll('[data-e2e^="view-more-"], [class*="ReplyActionText"]').forEach(el => {
                        if (!el.dataset._ttc) { el.click(); el.dataset._ttc = '1'; clicked++; }
                    });
                    return clicked;
                })()""")
            except Exception:
                pass

            # Write JSONL incrementally (idempotent)
            records = [r.to_dict() for r in all_raw_]
            written = write_jsonl(str(out_path_), records)
            job_state_["comments_seen"] = len(seen_ids_)
            job_state_["comments_written"] = written
            job_state_["cursor"] = i
            save_job(video_id_, job_state_)

            iter_new = dom_added + cdp_added + api_added
            # Telemetri [COLLECT] — visible = jumlah DOM row saat ini (bukan total unik)
            print(f"[COLLECT] iteration={i+1} visible={visible} dom=+{dom_added} cdp=+{cdp_added} api=+{api_added} total={len(all_raw_)}")
            new += iter_new
            if iter_new == 0:
                stale += 1
            else:
                stale = 0

            if len(all_raw_) >= max_comments_:
                print(f"[collector] Cap {max_comments_} komentar. Stop.")
                break
            if stale >= STALE_LIMIT and i >= 10:
                print(f"[collector] stale stop at iter {i+1} (no new unique IDs for {STALE_LIMIT} iters)")
                break

            try:
                await tab_.evaluate(SCROLL_JS)
            except Exception:
                pass
            await asyncio.sleep(2)
        return new

    for _pass in range(3):
        if _pass > 0:
            print(f"[collector] pass {_pass+1}: panel gagal / 0 komentar — reload halaman")
            await tab.get(video_url)
            await asyncio.sleep(6)
            video_ctx = await extract_video_context(tab)
            video_ctx["video_id"] = video_id
            video_ctx["video_url"] = video_url

        # Open comment panel (retry — element muncul async)
        click_result = "not found"
        for attempt in range(5):
            try:
                click_result = await tab.evaluate(CLICK_COMMENT_PANEL_JS)
            except Exception:
                click_result = "eval error"
            print(f"[collector] open panel: {click_result}")
            if click_result != "not found":
                break
            await asyncio.sleep(2)
        if click_result == "not found":
            va = await tab.evaluate(VIEW_ALL_JS)
            print(f"[collector] view-all: {va}")
            await asyncio.sleep(2)

        # Wait for comments load
        print("[collector] waiting for comment content...")
        for _ in range(15):
            levels = await tab.evaluate('document.querySelectorAll(\'[data-e2e^="comment-level-"]\').length')
            if levels > 0:
                break
            await asyncio.sleep(2)

        pass_new = await _capture_pass(tab, video_ctx, cdp_pages, all_raw, seen_ids,
                                       out_path, video_id, job_state,
                                       max_scrolls, max_comments)
        if pass_new > 0 or pass_new == "blocked":
            break

    # Drain any remaining CDP reply pages
    while cdp_pages:
        page = cdp_pages.pop(0)
        for api_comment in page.get("comments", []):
            cid = api_comment.get("cid", "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            r = raw_from_api(api_comment, video_ctx, method="cdp")
            all_raw.append(r)

    # Drain reply pages yang tersisa, lalu tulis final
    while cdp_pages:
        page = cdp_pages.pop(0)
        for api_comment in page.get("comments", []):
            cid = api_comment.get("cid", "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            r = raw_from_api(api_comment, video_ctx, method="cdp")
            all_raw.append(r)

    final_written = write_jsonl(str(out_path), [r.to_dict() for r in all_raw])

    # ── Collection completeness (poin #4/#5) ──
    captured = len(all_raw)
    if reported_count > 0:
        coverage = round(captured / reported_count, 3)
        if coverage >= 1.0:
            collection_status = "complete"
        elif coverage >= 0.5:
            collection_status = "partial"
        else:
            collection_status = "partial"   # <50% tetap partial; failed khusus error
    else:
        coverage = None
        collection_status = "unknown"

    job_state["status"] = "done"
    job_state["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    job_state["reported_comment_count"] = reported_count
    job_state["captured_comment_count"] = captured
    job_state["coverage"] = coverage
    job_state["collection_status"] = collection_status
    if captured < reported_count:
        job_state["collection_reason"] = "no_new_comments"
    save_job(video_id, job_state)

    # Telemetri CDP (poin #6/#9)
    print(f"[cdp-count] observed={cdp_counters['observed']} comment_like={cdp_counters['comment_like']} "
          f"parsed_ok={cdp_counters['parsed_ok']} comments={cdp_counters['comments']}")
    print(f"[collector] Done. {captured} raw comments → {out_path}")
    print(f"[collector] reported={reported_count} captured={captured} coverage={coverage} status={collection_status}")
    browser.stop()

    return {
        "video_id": video_id,
        "output": str(out_path),
        "comments": captured,
        "job_id": job_state["job_id"],
        "reported_comment_count": reported_count,
        "coverage": coverage,
        "collection_status": collection_status,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
async def login_only():
    ensure_dirs()
    browser = await uc.start(headless=False, user_data_dir=str(PROFILE_DIR))
    tab = browser.main_tab
    await tab.get("https://www.tiktok.com/login")
    print("\n" + "=" * 60)
    print("  Login ke TikTok manually di browser.")
    print(f"  Session tersimpan di {PROFILE_DIR}/")
    print("  Tekan Ctrl+C setelah selesai.")
    print("=" * 60 + "\n")
    try:
        while True:
            await tab.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Session saved.")
    browser.stop()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Comment Collector")
    parser.add_argument("url", nargs="*", help="TikTok video URL(s)")
    parser.add_argument("--scrolls", type=int, default=60, help="Max scroll iterations")
    parser.add_argument("--max", type=int, default=300, help="Max comments")
    parser.add_argument("--resume", help="Resume job ID (video_id)")
    parser.add_argument("--login", action="store_true", help="Login only")
    args = parser.parse_args()

    if args.login or not args.url:
        asyncio.run(login_only())
    else:
        for u in args.url:
            print(f"\n########## COLLECT: {u} ##########\n")
            asyncio.run(collect_video(
                video_url=u,
                max_scrolls=args.scrolls,
                max_comments=args.max,
                resume=args.resume,
            ))


if __name__ == "__main__":
    main()
