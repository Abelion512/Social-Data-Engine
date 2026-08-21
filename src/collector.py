#!/usr/bin/env python3
"""
TikTok Data Acquisition Engine — Collector (Camoufox)

Browser: Camoufox (Playwright-based, anti-detect Firefox fingerprint).
Capture: DOM scrape + API fetch (via page.evaluate) + Playwright route intercept.
Replies: Recursive expand (max 5 rounds per iteration).
Images: Post-scrape enrichment via lazy-load scroll + re-scrape.

Usage:
  python collector.py <tiktok_url> [--scrolls N] [--max N] [--resume job_id]
  python collector.py --login
"""
from __future__ import annotations
import asyncio
import json
import re
import os
import sys
import time
import random
from pathlib import Path
from typing import List, Dict, Optional, Union

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Installable/portable data root — set $SDE_DATA_DIR untukarahkan ke mana saja
# (konsep portability sama .venv). Default → repo-root/data.
DATA_DIR = Path(os.environ.get("SDE_DATA_DIR") or (_ROOT / "data"))

from src.browser_selector import BrowserSession
from src.harness.human import (
    apply_stealth, resolve_captcha_if_present, ahuman_delay,
    human_click, human_scroll,
)
from src.tiktok_schema import (
    Author,
    RawComment,
    PaginationState,
    AcquisitionMetrics,
    TerminationReason,
    raw_from_api,
    raw_from_dom,
    write_jsonl,
    append_raw_records,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"
# DATA_DIR sudah didefinisikan di atas (SDE_DATA_DIR env, default repo data) —
# semua sub-path berikut ikut portabel.
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
    """Save checkpoint atomically via temporary file replacement."""
    ensure_dirs()
    p = JOB_DIR / f"{video_id}.json"
    temp_p = JOB_DIR / f"{video_id}.json.tmp"
    temp_p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    temp_p.replace(p)


def load_job(video_id: str) -> Optional[dict]:
    p = JOB_DIR / f"{video_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ── Video context probe ────────────────────────────────────────────────────────
async def extract_video_context(page) -> dict:
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
        data = await page.evaluate(ctx_js)
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            print(f"[collector] video context: unexpected shape {type(data)}")
            return {"video_id": "", "video_url": "", "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""}
        video_url = data.get("url", "")
        m = re.search(r"/(?:video|photo)/(\d+)", video_url)
        video_id = m.group(1) if m else ""
        return {
            "video_id": video_id,
            "video_url": video_url,
            "caption": data.get("caption", ""),
            "hashtags": data.get("hashtags", []),
            "creator": data.get("creator", ""),
            "create_time": int(time.time()),
            "transcription": ""
        }
    except Exception as e:
        print(f"[collector] video context error: {e}")
        return {"video_id": "", "video_url": "", "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""}


# ── DOM scraper (inline JS) ───────────────────────────────────────────────────
DOM_SCRAPE_JS = r"""(() => {
        const out = [];
        const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
        items.forEach(el => {
            const raw = el.textContent ? el.textContent.replace(/\s+/g, ' ').trim() : '';
            var imgs = [];
            el.querySelectorAll('img').forEach(function(img) {
                var src = img.src || img.getAttribute('data-src') || '';
                if (src && !src.includes('static/') && !src.includes('avatar') &&
                    !src.includes('profile/') && !src.includes('icon') &&
                    (src.includes('tiktokcdn') || src.includes('iatura') ||
                     src.match(/\.(jpg|jpeg|png|webp)/i))) {
                    imgs.push(src);
                }
            });
            el.querySelectorAll('div[style]').forEach(function(d) {
                var bg = d.style.backgroundImage || '';
                var m = bg.match(/url\(["']?([^"')]+)["']?\)/);
                if (m && m[1] && (m[1].includes('tiktokcdn') || m[1].includes('iatura')) &&
                    !m[1].includes('avatar') && !m[1].includes('icon')) {
                    imgs.push(m[1]);
                }
            });
            el.querySelectorAll('video[poster]').forEach(function(v) {
                var p = v.poster || '';
                if (p && p.includes('tiktokcdn')) imgs.push(p);
            });
            imgs = imgs.slice(0, 6);
            // Sticker = GIF (bukan static/avatar) — TikTok kirim sticker GIF via <img>.
            var imgSrcs = [];
            el.querySelectorAll('img').forEach(function(img) {
                var s = img.src || img.getAttribute('data-src') || img.getAttribute('src') || '';
                if (s) imgSrcs.push(s);
            });
            var sticker = null;
            for (var si = 0; si < imgSrcs.length; si++) {
                if (imgSrcs[si].indexOf('.gif') !== -1 && imgs.indexOf(imgSrcs[si]) === -1) {
                    sticker = imgSrcs[si];
                    break;
                }
            }
            // Voice note = <audio> (atau <video> muted yang sama asalnya suara)
            var voice = [];
            el.querySelectorAll('audio').forEach(function(a) {
                var s = a.src || a.getAttribute('src') || '';
                if (s) voice.push(s);
            });
            if ((!raw || raw.length < 3) && imgs.length === 0 && voice.length === 0 && !sticker) return;
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
                audio: voice,
                sticker: sticker,
                comment_id: cid,
            });
        });
        const seen = new Set();
        return JSON.stringify(out.filter(o => (seen.has(o.raw + '|' + o.images.join(',')) ? false : (seen.add(o.raw + '|' + o.images.join(',')), true))));
    })()"""

SCROLL_JS = r"""(() => {
        var cont = null;
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
        var sl = document.querySelector('[data-e2e="comment-list"]');
        if (sl) {
            var p = sl.parentElement;
            while (p && p.scrollHeight <= p.clientHeight + 10) p = p.parentElement;
            if (p) cont = p;
        }
        if (!cont) cont = document.querySelector('[class*="DivCommentListContainer"]');
        if (!cont) cont = document.querySelector('[class*="DivCommentMain"]');
        if (!cont) cont = document.querySelector('[class*="CommentListContainer"]');
        if (!cont) {
            var cands = Array.from(document.querySelectorAll('div')).filter(e => {
                return e.scrollHeight > e.clientHeight + 50 &&
                       e.querySelectorAll('[data-e2e^="comment-level-"]').length > 0;
            }).sort((a, b) => b.scrollHeight - a.scrollHeight);
            if (cands.length) cont = cands[0];
        }
        document.querySelectorAll('[data-e2e*="load-more"], [class*="LoadMore"], [data-e2e*="comment-load"], [data-e2e*="view-more-"]').forEach(el => { try { el.click(); } catch (e) {} });
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
    // Challenge sekarang TikTok pakai modal overlay (bukan redirect /verify URL):
    // cek element CAPTCHA/verification/slider + teks challenge di body.
    var modal = !!document.querySelector(
        '[class*="captcha"],[class*="Captcha"],[class*="verify"],[class*="Verify"],'
        + '[class*="slider"],[class*="Slider"],#tiktok-verify,[data-e2e="captcha"],'
        + 'iframe[src*="captcha"],iframe[src*="verify"],[class*="verification"],'
        + '[class*="challenge"],[class*="Challenge"]'
    );
    var blocked = /login|verify|captcha|challenge/i.test(u)
               || (/Verify|Verification|captcha/i.test(bt) && /human|robot/i.test(bt))
               || /Please verify|are you a human|security check|captcha/i.test(bt.slice(0,400))
               || modal;
    return JSON.stringify({ url: u, blocked: blocked, modal: modal });
})()"""

IMAGE_ENRICH_JS = r"""(comment_ids) => {
        const out = {};
        const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
        items.forEach(el => {
            let cid = el.getAttribute('data-e2e') || '';
            if (!cid || /^comment-level-\d+$/.test(cid)) return;
            if (!comment_ids.includes(cid)) return;
            var imgs = [];
            el.querySelectorAll('img').forEach(function(img) {
                var src = img.src || img.getAttribute('data-src') || '';
                if (src && !src.includes('static/') && !src.includes('avatar') &&
                    !src.includes('profile/') && !src.includes('icon') &&
                    (src.includes('tiktokcdn') || src.includes('iatura') ||
                     src.match(/\.(jpg|jpeg|png|webp)/i))) {
                    imgs.push(src);
                }
            });
            el.querySelectorAll('div[style]').forEach(function(d) {
                var bg = d.style.backgroundImage || '';
                var m = bg.match(/url\(["']?([^"')]+)["']?\)/);
                if (m && m[1] && (m[1].includes('tiktokcdn') || m[1].includes('iatura')) &&
                    !m[1].includes('avatar')) {
                    imgs.push(m[1]);
                }
            });
            el.querySelectorAll('video[poster]').forEach(function(v) {
                var p = v.poster || '';
                if (p && p.includes('tiktokcdn')) imgs.push(p);
            });
            if (imgs.length > 0) out[cid] = imgs.slice(0, 6);
        });
        return JSON.stringify(out);
    })()"""


# ── Playwright route intercept: capture comment API responses ──────────────────
# Menggantikan CDP Fetch intercept dari nodriver. Playwright route() bisa
# intercept response body tanpa race condition.
async def _setup_route_intercept(page, captured_pages: list, counters: dict):
    """Setup page.route() untuk intercept TikTok comment API responses.

    Route callback: baca response body → parse → simpan ke captured_pages.
    Must be async: Playwright's page.route() is a coroutine and must be
    awaited, otherwise the route intercept is silently skipped (discovered
    via live test: 'RuntimeWarning: coroutine Page.route was never awaited').
    """
    comment_api_re = re.compile(r"(comment|aweme).*list")

    async def _on_route(route):
        url = route.request.url
        counters["observed"] += 1
        if not comment_api_re.search(url):
            await route.continue_()
            return

        counters["comment_like"] += 1
        is_reply = "/reply/" in url or "comment/list/reply" in url
        # TikTok reply objects do NOT embed their parent — the parent cid is
        # the `comment_id=` query param of the /comment/list/reply request URL.
        # Parse it here so threaded replies can be reconstructed downstream.
        m = re.search(r"[?&]comment_id=([0-9A-Za-z]+)", url)
        parent_id = m.group(1) if m else ""

        try:
            response = await route.fetch()
            body = await response.text()
            data = json.loads(body)
        except json.JSONDecodeError as e:
            err = f"json decode: {e}"[:100]
            counters["parse_errors"] = counters.get("parse_errors", 0) + 1
            if counters.get("parsed_ok", 0) == 0:
                print(f"[route] parse err ({url[-60:]}): {err}")
            await route.continue_()
            return
        except Exception as e:
            err = str(e)[:100]
            counters["fetch_errors"] = counters.get("fetch_errors", 0) + 1
            if counters.get("parsed_ok", 0) == 0:
                print(f"[route] body err ({url[-60:]}): {err}")
            await route.continue_()
            return

        await route.continue_()

        comments = data.get("comments") if isinstance(data, dict) else None
        if not isinstance(comments, list):
            page_data = {
                "url": url,
                "status_code": response.status,
                "comments": [],
                "has_more": 0 if not isinstance(data, dict) else data.get("has_more", 0),
                "cursor": 0 if not isinstance(data, dict) else data.get("cursor", 0),
                "is_reply": is_reply,
                "parent_comment_id": parent_id,
                "status_msg": data.get("status_msg") if isinstance(data, dict) else str(data)[:100],
            }
            captured_pages.append(page_data)
            return

        counters["parsed_ok"] = counters.get("parsed_ok", 0) + 1
        counters["comments"] = counters.get("comments", 0) + len(comments)
        page_data = {
            "url": url,
            "status_code": response.status,
            "comments": comments,
            "has_more": data.get("has_more", 0),
            "cursor": data.get("cursor", 0),
            "is_reply": is_reply,
            "parent_comment_id": parent_id,
            "status_msg": data.get("status_msg", "ok"),
        }
        captured_pages.append(page_data)
        label = "reply" if is_reply else "comment"
        print(f"[route] {label} page: +{len(page_data['comments'])}")

    await page.route("**/*", _on_route)    # async — Playwright route intercept
    print("[collector] Playwright route intercept registered")


# ── Helpers ────────────────────────────────────────────────────────────────────
def _b36(n: int) -> str:
    """JS-compatible toString(36)."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


async def _probe_reported_count(page) -> int:
    js = r"""(() => {
        const q = (s) => document.querySelector(s);
        const findNum = (el) => {
            const t = (el.getAttribute('title') || el.textContent || el.innerText || '').trim();
            const m = t.match(/(\d[\d.,]*)\s*(?:comments?|komentar)/i);
            return m ? parseInt(m[1].replace(/[.,]/g, ''), 10) || 0 : 0;
        };
        // /photo/ panel: angka kadang di <span> terpisah dari label "Comments"
        const c = q('[data-e2e="comment-count"]');
        if (c) {
            let n = findNum(c);
            if (!n) { const par = (c.closest('button,div') || c.parentElement); if (par) n = findNum(par); }
            if (n) return n;
        }
        // aria-label / data-e2e yang mengandung angka
        for (const b of document.querySelectorAll('button, [data-e2e]')) {
            const l = b.getAttribute('aria-label') || '';
            const m = l.match(/(\d[\d.,]*)\s*(?:comments?|komentar)/i);
            if (m) return parseInt(m[1].replace(/[.,]/g, ''), 10) || 0;
        }
        // body panel header
        const bt = document.body ? document.body.innerText : '';
        const m = bt.match(/(\d[\d.,]*)\s*(?:comments?|komentar)/i);
        if (m) return parseInt(m[1].replace(/[.,]/g, ''), 10) || 0;
        return 0;
    })()"""
    try:
        r = await page.evaluate(js)
        if isinstance(r, list):
            r = r[0] if r else 0
        return int(r or 0)
    except Exception:
        return 0


async def fetch_comments_api(page, video_id: str, cursor: int = 0, count: int = 50, retries: int = 3) -> dict:
    """Fetch komentar dari TikTok API via evaluate (same-origin cookie).

    Skala 2K: count=50/page → ~40 page buat 2000 komentar (TikTok web API
    silently returns empty/has_more=0 if count > 50). Retry/backoff
    (anti rate-limit/challenge) — biarkan caller terus scroll + coba lagi.
    """
    url_pattern = f"https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id={video_id}&count={count}&cursor={cursor}&comment_style=2&from=web&device_platform=web&channel=normal&enter_from=comment_detail_page"
    js = f"""async () => {{
        const url = `{url_pattern}`;
        try {{
            const r = await fetch(url, {{ credentials: 'include', headers: {{ 'accept': 'application/json, text/plain, */*', 'sec-fetch-site': 'same-origin' }} }});
            const text = await r.text();
            return JSON.stringify({{ status_code: r.status, body: text, url: url }});
        }} catch (e) {{
            return JSON.stringify({{ error: String(e), error_type: 'fetch_failure', url: url, status_code: 'fetch_err' }});
        }}
    }}"""
    last_err = "empty result"
    last_err_type = "fetch_failure"
    last_status_code: Union[int, str] = "eval_err"
    for attempt in range(retries):
        try:
            out = await page.evaluate(js)
        except Exception as e:
            last_err = f"evaluate: {e}"
            last_err_type = "fetch_failure"
            last_status_code = "eval_err"
            await ahuman_delay(3.0, 6.0)
            continue
        if isinstance(out, list):
            out = out[0] if out else None

        parsed_eval: dict = {}
        if isinstance(out, dict):
            parsed_eval = out
        elif isinstance(out, str) and out.strip():
            try:
                parsed_eval = json.loads(out)
            except Exception as e:
                last_err = f"eval json parse: {e}"
                last_err_type = "parse_failure"
                last_status_code = "parse_err"
                await ahuman_delay(2.0, 4.0)
                continue
        else:
            last_err = "empty eval response"
            last_err_type = "fetch_failure"
            last_status_code = "empty"
            await ahuman_delay(2.0, 4.0)
            continue

        last_status_code = parsed_eval.get("status_code", 200)
        if parsed_eval.get("error"):
            last_err = str(parsed_eval["error"])[:100]
            last_err_type = parsed_eval.get("error_type", "fetch_failure")
            # mungkin challenge overlay blokir request — coba resolve, lalu retry
            try:
                from src.harness.human import resolve_captcha_if_present
                await resolve_captcha_if_present(page, attempts=1)
            except Exception:
                pass
            await ahuman_delay(4.0, 7.0)
            continue

        body_text = parsed_eval.get("body")
        if body_text is not None:
            try:
                j = json.loads(body_text) if isinstance(body_text, str) else body_text
                if not isinstance(j, dict):
                    last_err = f"malformed payload ({type(j).__name__})"
                    last_err_type = "parse_failure"
                    await ahuman_delay(2.0, 4.0)
                    continue
                status_msg = str(j.get("status_msg", "")).lower()
                status_code = j.get("status_code", last_status_code)
                if any(w in status_msg for w in ("verify", "captcha", "block", "login", "security")) or status_code in (10001, 10002):
                    return {
                        "url": parsed_eval.get("url", url_pattern),
                        "status_code": status_code,
                        "status_msg": status_msg,
                        "error": f"API challenge/blocked: {status_msg or status_code}",
                        "error_type": "auth_blocked",
                        "comments": [],
                    }
                return {
                    "url": parsed_eval.get("url", url_pattern),
                    "status_code": status_code,
                    "status_msg": status_msg,
                    "comments": j.get("comments") or [],
                    "has_more": j.get("has_more") or 0,
                    "cursor": j.get("cursor") or 0,
                    "status": "ok",
                }
            except json.JSONDecodeError as e:
                last_err = f"json decode payload: {e}"
                last_err_type = "parse_failure"
                await ahuman_delay(2.0, 4.0)
                continue
        elif "comments" in parsed_eval:
            return {
                "url": parsed_eval.get("url", url_pattern),
                "status_code": 200,
                "comments": parsed_eval.get("comments") or [],
                "has_more": parsed_eval.get("has_more") or 0,
                "cursor": parsed_eval.get("cursor") or 0,
                "status": "ok",
            }

    return {
        "url": url_pattern,
        "status_code": last_status_code,
        "error": last_err,
        "error_type": last_err_type,
        "comments": [],
    }


# ── Main capture loop ──────────────────────────────────────────────────────────
async def _capture_pass(
    page,
    video_ctx: dict,
    captured_pages: list,
    all_raw: List[RawComment],
    seen_ids: set,
    out_path: Path,
    video_id: str,
    job_state: dict,
    pagination_state: Optional[PaginationState] = None,
    max_scrolls: int = 200,
    max_comments: int = 2000,
) -> int:
    """Satu pass capture: DOM + route intercept + scroll + API fetch with incremental persistence."""
    if pagination_state is None:
        pagination_state = PaginationState()

    new = 0

    for i in range(max_scrolls):
        # human_act: jeda acak sebelum setiap scroll (bukan bot yang pola-pola)
        await ahuman_delay(0.8, 2.2)

        new_batch_dicts: List[dict] = []
        dom_added = 0
        try:
            dom_str = await page.evaluate(DOM_SCRAPE_JS)
            if isinstance(dom_str, list):
                dom_str = dom_str[0] if dom_str else None
            rows = json.loads(dom_str) if isinstance(dom_str, str) else (dom_str or [])
        except Exception as e:
            print(f"[collector] DOM scrape err: {e}")
            rows = []
        visible = len(rows)

        for row in rows:
            cid = row.get("comment_id", "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            r = raw_from_dom(row, video_ctx)
            all_raw.append(r)
            new_batch_dicts.append(r.to_dict())
            dom_added += 1

        # Drain route-intercepted pages
        cdp_added = 0
        while captured_pages:
            pg = captured_pages.pop(0)
            comments_batch = pg.get("comments", [])
            pg_url = pg.get("url", "route:comment/list")
            pg_status = pg.get("status_code", 200)
            pg_cursor = pg.get("cursor", 0)
            pg_has_more = pg.get("has_more", 0)
            route_dups = 0
            unique_before = len(seen_ids)
            for api_comment in comments_batch:
                cid = api_comment.get("cid", "")
                if cid:
                    if cid in seen_ids:
                        route_dups += 1
                        continue
                    fprint = str(api_comment.get("text", ""))[:120] + "|" + str((api_comment.get("user", {}) or {}).get("unique_id", ""))
                    h = 0
                    for ch in fprint:
                        h = (h * 31 + ord(ch)) & 0x7fffffff
                    dom_alias = "dom_" + _b36(h)
                    if dom_alias in seen_ids:
                        seen_ids.add(cid)
                        route_dups += 1
                        continue
                    seen_ids.add(cid)
                r = raw_from_api(api_comment, video_ctx, method="route",
                                 parent_comment_id=pg.get("parent_comment_id", ""))
                all_raw.append(r)
                new_batch_dicts.append(r.to_dict())
                cdp_added += 1

            unique_after = len(seen_ids)
            pagination_state.record_diagnostic(
                request_url_pattern=pg_url[:150],
                current_cursor=pg_cursor,
                response_status=pg_status,
                response_item_count=len(comments_batch),
                has_more=bool(pg_has_more),
                next_cursor=pg_cursor,
                unique_before=unique_before,
                unique_after=unique_after,
                total_unique_comments=unique_after,
                retry_count=pagination_state.retry_count,
                termination_reason=pagination_state.termination_reason,
                source="route",
                page_index=pagination_state.page_index,
                extra={"is_reply": pg.get("is_reply", False), "duplicates": route_dups, "status_msg": pg.get("status_msg", "")},
            )

        # API fetch (same-origin, proactive pagination)
        api_added = 0
        api_duplicates = 0
        if pagination_state.has_more and len(all_raw) < max_comments:
            cur_sent = pagination_state.cursor
            unique_before = len(seen_ids)
            pg = await fetch_comments_api(page, video_id, cur_sent) or {}
            pg_url = pg.get("url") or f"api/comment/list/?aweme_id={video_id}&cursor={cur_sent}"
            pg_status = pg.get("status_code", 200 if not pg.get("error") else "err")

            if pg.get("error"):
                err_type = pg.get("error_type", "fetch_failure")
                pagination_state.record_error(pg["error"], error_type=err_type)
                print(f"[api] {err_type} (retry {pagination_state.retry_count}/{pagination_state.max_retries}): {pg['error'][:80]}")
                pagination_state.record_diagnostic(
                    request_url_pattern=pg_url[:150],
                    current_cursor=cur_sent,
                    response_status=pg_status,
                    response_item_count=0,
                    has_more=pagination_state.has_more,
                    next_cursor=None,
                    unique_before=unique_before,
                    unique_after=unique_before,
                    total_unique_comments=unique_before,
                    retry_count=pagination_state.retry_count,
                    termination_reason=pagination_state.termination_reason,
                    source="api",
                    page_index=pagination_state.page_index,
                    extra={"error": pg.get("error"), "error_type": err_type},
                )
            else:
                comments = pg.get("comments") or []
                for api_comment in comments:
                    cid = api_comment.get("cid", "")
                    if cid and cid in seen_ids:
                        api_duplicates += 1
                        continue
                    if cid:
                        seen_ids.add(cid)
                    r = raw_from_api(api_comment, video_ctx, method="api")
                    all_raw.append(r)
                    new_batch_dicts.append(r.to_dict())
                    api_added += 1
                pagination_state.process_page(
                    comments=comments,
                    next_cursor=pg.get("cursor"),
                    has_more=pg.get("has_more"),
                    deduplicated=api_duplicates,
                )
                unique_after = len(seen_ids)
                pagination_state.record_diagnostic(
                    request_url_pattern=pg_url[:150],
                    current_cursor=cur_sent,
                    response_status=pg_status,
                    response_item_count=len(comments),
                    has_more=bool(pg.get("has_more")),
                    next_cursor=pg.get("cursor"),
                    unique_before=unique_before,
                    unique_after=unique_after,
                    total_unique_comments=unique_after,
                    retry_count=pagination_state.retry_count,
                    termination_reason=pagination_state.termination_reason,
                    source="api",
                    page_index=pagination_state.page_index,
                    extra={"duplicates": api_duplicates, "status_msg": pg.get("status_msg", "")},
                )

        # Expand nested replies — REKURSIF (max 5 round). Pakai `human_click`
        # (bukan DOM .click()) karena React listener + /photo/ challenge butuh
        # realistik mouse event. Expand memicu fetch /comment/list/reply (route
        # intercept) → chain ke nested reply & scale ke 2K.
        _REPLY_SELECTORS = [
            '[data-e2e^="view-more-"]', '[data-e2e*="reply-more"]',
            '[class*="ReplyActionText"]', '[class*="ViewActionText"]',
            '[data-e2e="reply-count"]', '[data-e2e*="show-more-reply"]',
            '[data-e2e*="reply"] button',
        ]
        for _expand_round in range(5):
            clicked = 0
            for sel in _REPLY_SELECTORS:
                try:
                    n = await page.locator(sel).count()
                except Exception:
                    n = 0
                for idx in range(n):
                    if await human_click(page, page.locator(f"{sel} >> nth={idx}")):
                        clicked += 1
            # fallback: text "Lihat X balasan / View X replies / Lihat semua"
            try:
                matches = await page.locator(
                    "text=/Lihat (semua )?\\d+ balas|View (all )?\\d+ repl/i").all()
            except Exception:
                matches = []
            for el in matches:
                if await human_click(page, el):
                    clicked += 1
            if clicked > 0:
                await ahuman_delay(0.6, 1.4)
            else:
                break

        # Image enrichment
        try:
            empty_img_cids = [r.comment_id for r in all_raw if not r.images and r.comment_id]
            if empty_img_cids:
                target_cids = empty_img_cids[-15:]
                enriched_js = await page.evaluate(IMAGE_ENRICH_JS, target_cids)
                if isinstance(enriched_js, list):
                    enriched_js = enriched_js[0] if enriched_js else None
                if isinstance(enriched_js, str) and enriched_js.strip():
                    enriched = json.loads(enriched_js)
                    img_found = 0
                    for r in all_raw:
                        if r.comment_id in enriched and not r.images:
                            r.images = enriched[r.comment_id]
                            img_found += 1
                    if img_found > 0:
                        print(f"[img] enriched {img_found} comments with images")
                if target_cids:
                    await page.evaluate(f"""(() => {{
                        var cid = '{target_cids[-1]}';
                        var el = document.querySelector('[data-e2e="' + cid + '"]');
                        if (el) el.scrollIntoView({{ block: 'center' }});
                    }})()""")
                    await asyncio.sleep(1)
        except Exception:
            pass

        # Write new records incrementally to disk with disk-backed deduplication
        if new_batch_dicts:
            append_raw_records(str(out_path), new_batch_dicts, seen_ids=None)

        # Checkpoint is ONLY persisted AFTER raw writes succeed
        pagination_state.record_items(len(seen_ids))
        job_state["comments_seen"] = len(seen_ids)
        job_state["comments_written"] = len(all_raw)
        job_state["cursor"] = pagination_state.cursor
        job_state["page_index"] = pagination_state.page_index
        job_state["has_more"] = pagination_state.has_more
        job_state["pagination"] = pagination_state.to_dict()
        job_state["metrics"] = pagination_state.metrics.to_dict()
        job_state["last_success_at"] = pagination_state.metrics.last_success_at
        if pagination_state.termination_reason:
            job_state["termination_reason"] = pagination_state.termination_reason
        save_job(video_id, job_state)

        iter_new = dom_added + cdp_added + api_added
        print(f"[COLLECT] iteration={i+1} visible={visible} dom=+{dom_added} route=+{cdp_added} api=+{api_added} total={len(all_raw)} cursor={pagination_state.cursor} has_more={pagination_state.has_more}")
        new += iter_new

        if pagination_state.check_cap(max_comments):
            print(f"[collector] Cap {max_comments} komentar tercapai. Stop.")
            break

        if not pagination_state.has_more:
            print(f"[collector] Pagination finished: reason={pagination_state.termination_reason}")
            break

        try:
            await page.evaluate(SCROLL_JS)
        except Exception:
            pass
        # Jika DOM-container scroll gagal (panel tertutup/challenge), viewport
        # scroll natural (human_scroll) tetap trigger TikTok lazy API fetch.
        await human_scroll(page, delta=random.randint(600, 1100), times=1)
        await asyncio.sleep(2)
    return new


# ── Main entry point ───────────────────────────────────────────────────────────
async def collect_video(
    video_url: str,
    max_scrolls: int = 200,
    max_comments: int = 2000,
    resume: Optional[str] = None,
    force_camoufox: bool = False,
) -> Dict:
    """Collector utama: ambil komentar dari satu video TikTok."""
    ensure_dirs()

    m = re.search(r"/(?:video|photo)/(\d+)", video_url)
    if not m:
        print(f"[!] Tidak bisa ekstrak video_id dari {video_url}")
        return {"error": "invalid_url", "video_url": video_url}
    video_id = m.group(1)
    print(f"[collector] Video ID: {video_id}")

    job_state = load_job(video_id) if resume else None
    if job_state:
        pag_dict = job_state.get("pagination") or {
            "cursor": job_state.get("cursor", 0),
            "page_index": job_state.get("page_index", 0),
            "items_seen": job_state.get("comments_seen", 0),
            "has_more": job_state.get("has_more", True),
            "termination_reason": job_state.get("termination_reason"),
            "metrics": job_state.get("metrics"),
        }
        pagination_state = PaginationState.from_dict(pag_dict)
        print(f"[collector] Resume job: cursor={pagination_state.cursor}, seen={pagination_state.items_seen}, page={pagination_state.page_index}")
    else:
        pagination_state = PaginationState()
        job_state = {
            "job_id": resume or f"job_{video_id}_{int(time.time())}",
            "video_id": video_id,
            "cursor": 0,
            "comments_seen": 0,
            "comments_written": 0,
            "status": "running",
            "pagination": pagination_state.to_dict(),
            "metrics": pagination_state.metrics.to_dict(),
        }
        job_state["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    out_path = raw_path(video_id)
    all_raw: List[RawComment] = []
    seen_ids: set = set()
    captured_pages: list = []
    route_counters = {"observed": 0, "comment_like": 0, "parsed_ok": 0, "comments": 0}

    # Load existing comments if resuming
    if resume and out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    cid = d.get("comment_id", "")
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        all_raw.append(RawComment(
                            video_id=d.get("video_id", video_id),
                            video_url=d.get("video_url", video_url),
                            comment_id=cid,
                            parent_comment_id=d.get("parent_comment_id", ""),
                            author=Author(
                                author_id=d.get("author_id", ""),
                                author_handle=d.get("author_handle", ""),
                                display_name=d.get("display_name", ""),
                            ),
                            text_raw=d.get("text_raw", ""),
                            likes=d.get("likes", 0),
                            reply_count=d.get("reply_count", 0),
                            create_time=d.get("create_time", 0),
                            images=d.get("images", []),
                            audio=d.get("audio", []),
                            sticker=d.get("sticker"),
                            capture_method=d.get("capture_method", ""),
                            captured_at=d.get("captured_at", ""),
                            video_context=d.get("video_context", {}),
                        ))
            pagination_state.record_items(len(seen_ids))
            print(f"[collector] Resumed {len(all_raw)} existing raw comments from {out_path}")
        except Exception as e:
            print(f"[collector] Error loading existing raw records: {e}")

    # ── Browser selection: CDP → user browser → Camoufox fallback ──
    session = BrowserSession()
    try:
        if not await session.connect(force_camoufox=force_camoufox):
            return {"error": "browser_connect_failed"}
        page = session.page
        if page is None:
            return {"error": "browser_connect_failed_no_page"}
        mode = "CDP" if session.is_cdp else "Camoufox"
        print(f"[collector] Browser mode: {mode}")

        # human_act first: stealth fingerprint overrides (defense-in-depth atop
        # Camoufox's built-in anti-detect). Applied before nav so TikTok's bot
        # detection never sees a stock headless signature.
        stealth = await apply_stealth(page)
        print(f"[human] stealth: {stealth}")

        # Setup route intercept (replaces CDP Fetch intercept)
        await _setup_route_intercept(page, captured_pages, route_counters)

        # Navigate with retry
        for attempt in range(3):
            print(f"[collector] Navigating (attempt {attempt+1})...")
            await page.goto(video_url, wait_until="commit", timeout=60000)
            await asyncio.sleep(6)

            status = await page.evaluate(CHECK_BLOCK_JS)
            try:
                s = json.loads(status) if isinstance(status, str) else (status if isinstance(status, dict) else {})
            except Exception:
                s = {}
            if s.get("blocked"):
                # human_act first: rather than bail on verify/captcha, ATTEMPT to
                # resolve it (slider drag via vision/mouse, image captcha via
                # 9Router Gemini vision). Only bail if resolution fails.
                print(f"[human] TikTok blocked (verify/captcha) at {s.get('url', '?')} — attempting resolution")
                res = await resolve_captcha_if_present(page, attempts=2)
                print(f"[human] captcha resolve result: {res}")
                if res.get("solved"):
                    print("[human] captcha solved — continuing collection")
                    await asyncio.sleep(3)
                    continue
                print(f"[!] TikTok blocked (login/verify) at {s.get('url', '?')} — captcha tidak terpecahkan")
                pagination_state.record_auth_block("TikTok captcha/login challenge unresolved")
                job_state["status"] = "blocked"
                job_state["termination_reason"] = "auth_blocked"
                job_state["metrics"] = pagination_state.metrics.to_dict()
                job_state["pagination"] = pagination_state.to_dict()
                save_job(video_id, job_state)
                return {
                    "error": "blocked",
                    "status": "blocked",
                    "captcha": res,
                    "termination_reason": "auth_blocked",
                    "metrics": pagination_state.metrics.to_dict(),
                    "pagination": pagination_state.to_dict(),
                }
            if attempt < 2:
                await asyncio.sleep(3)

        # Extract video context
        video_ctx = await extract_video_context(page)
        video_ctx["video_id"] = video_id
        video_ctx["video_url"] = video_url

        reported_count = await _probe_reported_count(page)

        # ── Capture passes ──
        for _pass in range(3):
            if _pass > 0:
                print(f"[collector] pass {_pass+1}: panel gagal / 0 komentar — reload halaman")
                await page.goto(video_url, wait_until="commit", timeout=60000)
                await asyncio.sleep(6)
                video_ctx = await extract_video_context(page)
                video_ctx["video_id"] = video_id
                video_ctx["video_url"] = video_url

            # Open comment panel
            open_sel = '[data-e2e="comment-icon"], [data-e2e="comment-count"]'
            try:
                await page.wait_for_selector(open_sel, state="attached", timeout=15000)
            except Exception:
                pass  # tetap coba klik
            click_result = "not found"
            for attempt in range(5):
                try:
                    await page.click(open_sel, timeout=5000)
                    click_result = "clicked via page.click"
                except Exception as e:
                    click_result = f"click err: {str(e)[:40]}"
                print(f"[collector] open panel (attempt {attempt+1}): {click_result}")
                levels = await page.evaluate('document.querySelectorAll(\'[data-e2e^="comment-level-"]\').length')
                if levels > 0:
                    print(f"[collector] ✓ comment panel terbuka ({levels} level items)")
                    break
                await asyncio.sleep(2)
            if click_result not in ("clicked via page.click",):
                va = await page.evaluate(VIEW_ALL_JS)
                print(f"[collector] view-all: {va}")
                await asyncio.sleep(2)

            # Wait for comments
            print("[collector] waiting for comment content...")
            for _ in range(15):
                levels = await page.evaluate('document.querySelectorAll(\'[data-e2e^="comment-level-"]\').length')
                if levels > 0:
                    break
                await asyncio.sleep(2)

            await _capture_pass(
                page, video_ctx, captured_pages, all_raw,
                seen_ids, out_path, video_id, job_state,
                pagination_state=pagination_state,
                max_scrolls=max_scrolls, max_comments=max_comments,
            )
            # Break if comments collected or if pagination is completed
            if len(all_raw) > 0 or not pagination_state.has_more:
                break

        # Drain any remaining intercepted pages
        remaining_new = []
        while captured_pages:
            pg = captured_pages.pop(0)
            for api_comment in pg.get("comments", []):
                cid = api_comment.get("cid", "")
                if cid and cid in seen_ids:
                    continue
                if cid:
                    seen_ids.add(cid)
                r = raw_from_api(api_comment, video_ctx, method="route",
                                 parent_comment_id=pg.get("parent_comment_id", ""))
                all_raw.append(r)
                remaining_new.append(r.to_dict())

        if remaining_new:
            append_raw_records(str(out_path), remaining_new, seen_ids=None)

        # Collection completeness
        captured = len(all_raw)
        if reported_count > 0:
            coverage = round(captured / reported_count, 3)
            collection_status = "complete" if coverage >= 1.0 else ("partial" if captured > 0 else "empty")
        else:
            coverage = None
            collection_status = "complete" if captured > 0 else "empty"

        job_state["status"] = "done"
        job_state["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        job_state["reported_comment_count"] = reported_count
        job_state["captured_comment_count"] = captured
        job_state["coverage"] = coverage
        job_state["collection_status"] = collection_status
        job_state["termination_reason"] = pagination_state.termination_reason or (
            TerminationReason.NORMAL_COMPLETION if collection_status == "complete" else "finished"
        )
        pagination_state.metrics.ended_at = job_state["ended_at"]
        job_state["metrics"] = pagination_state.metrics.to_dict()
        job_state["pagination"] = pagination_state.to_dict()
        save_job(video_id, job_state)

        print(f"[route-count] observed={route_counters['observed']} comment_like={route_counters['comment_like']} "
              f"parsed_ok={route_counters['parsed_ok']} comments={route_counters['comments']}")
        print(f"[collector] Done. {captured} raw comments → {out_path}")
        print(f"[collector] reported={reported_count} captured={captured} coverage={coverage} status={collection_status}")

    finally:
        await session.close()

    return {
        "video_id": video_id,
        "output": str(out_path),
        "comments": captured,
        "mode": "cdp" if session.is_cdp else "camoufox",
        "job_id": job_state["job_id"],
        "reported_comment_count": reported_count,
        "coverage": coverage,
        "collection_status": collection_status,
        "pagination": pagination_state.to_dict(),
        "metrics": pagination_state.metrics.to_dict(),
        "termination_reason": job_state.get("termination_reason"),
    }


async def collect_comments(url: str, **kwargs) -> List[RawComment]:
    """Helper for ProviderAdapter returning List[RawComment]."""
    max_scrolls = kwargs.get("scrolls", 200)
    max_c = kwargs.get("max", kwargs.get("max_comments", 2000))
    resume = kwargs.get("resume")
    force_camoufox = kwargs.get("force_camoufox", False)
    res = await collect_video(
        video_url=url,
        max_scrolls=max_scrolls,
        max_comments=max_c,
        resume=resume,
        force_camoufox=force_camoufox,
    )
    out_file = res.get("output")
    comments: List[RawComment] = []
    if out_file and os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                comments.append(RawComment(
                    video_id=d.get("video_id", ""),
                    video_url=d.get("video_url", ""),
                    comment_id=d.get("comment_id", ""),
                    parent_comment_id=d.get("parent_comment_id", ""),
                    author=Author(
                        author_id=d.get("author_id", ""),
                        author_handle=d.get("author_handle", ""),
                        display_name=d.get("display_name", ""),
                    ),
                    text_raw=d.get("text_raw", ""),
                    likes=d.get("likes", 0),
                    reply_count=d.get("reply_count", 0),
                    create_time=d.get("create_time", 0),
                    images=d.get("images", []),
                    audio=d.get("audio", []),
                    sticker=d.get("sticker"),
                    capture_method=d.get("capture_method", ""),
                    captured_at=d.get("captured_at", ""),
                    video_context=d.get("video_context", {}),
                ))
    return comments


# ── CLI ────────────────────────────────────────────────────────────────────────
async def login_only():
    ensure_dirs()
    print("\n" + "=" * 60)
    print("  Login ke TikTok manually di browser.")
    print("  Cookies tersimpan otomatis.")
    print("  Tekan Ctrl+C setelah selesai.")
    print("=" * 60 + "\n")
    session = BrowserSession()
    if not await session.connect(force_camoufox=True):
        print("[!] Gagal buka browser")
        return
    try:
        await session.page.goto("https://www.tiktok.com/login")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Session saved.")
    finally:
        await session.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Comment Collector")
    parser.add_argument("url", nargs="*", help="TikTok video URL(s)")
    parser.add_argument("--scrolls", type=int, default=200, help="Max scroll iterations")
    parser.add_argument("--max", type=int, default=2000, help="Max comments")
    parser.add_argument("--resume", help="Resume job ID (video_id)")
    parser.add_argument("--login", action="store_true", help="Login only")
    parser.add_argument("--camoufox", action="store_true", help="Force Camoufox (skip CDP detection)")
    parser.add_argument("--csv", action="store_true", help="Ekspor tambahan CSV (.csv) dari JSONL hasil")
    parser.add_argument("--detect", action="store_true", help="Detect running browsers via CDP")
    args = parser.parse_args()

    if args.detect:
        from src.browser_selector import _test_detect
        asyncio.run(_test_detect())
    elif args.login or not args.url:
        asyncio.run(login_only())
    else:
        from src.export import csv_from_jsonl
        for u in args.url:
            print(f"\n########## COLLECT: {u} ##########\n")
            r = asyncio.run(collect_video(
                video_url=u,
                max_scrolls=args.scrolls,
                max_comments=args.max,
                resume=args.resume,
                force_camoufox=args.camoufox,
            ))
            if args.csv and r and r.get("output"):
                csv_path = csv_from_jsonl(r["output"])
                try:
                    rc = sum(1 for _ in open(csv_path, encoding="utf-8")) - 1
                except Exception:
                    rc = 0
                print(f"[export] CSV -> {csv_path} ({rc} rows)")


if __name__ == "__main__":
    main()
