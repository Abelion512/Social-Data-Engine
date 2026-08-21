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
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.browser_selector import BrowserSession
from src.harness.human import apply_stealth, resolve_captcha_if_present, ahuman_delay
from src.tiktok_schema import (
    RawComment,
    raw_from_api,
    raw_from_dom,
    write_jsonl,
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
    return JSON.stringify({ url: u, blocked: /login|verify/i.test(u) || (/Verify/.test(bt) && /human/.test(bt)) });
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
        except Exception as e:
            err = str(e)[:100]
            if counters["parsed_ok"] == 0:
                print(f"[route] body err ({url[-60:]}): {err}")
            await route.continue_()
            return

        await route.continue_()

        comments = data.get("comments") if isinstance(data, dict) else None
        if not isinstance(comments, list):
            return

        counters["parsed_ok"] += 1
        counters["comments"] += len(comments)
        page_data = {
            "comments": comments,
            "has_more": data.get("has_more", 0),
            "cursor": data.get("cursor", 0),
            "is_reply": is_reply,
            "parent_comment_id": parent_id,
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
        r = await page.evaluate(js)
        if isinstance(r, list):
            r = r[0] if r else 0
        return int(r or 0)
    except Exception:
        return 0


async def fetch_comments_api(page, video_id: str, cursor: int = 0) -> dict:
    """Fetch komentar dari TikTok API via evaluate (same-origin cookie)."""
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
        # Playwright's page.evaluate auto-awaits async expressions;
        # `await_promise=` is NOT a valid kwarg — live test surfaced it as:
        # "Page.evaluate() got an unexpected keyword argument 'await_promise'".
        out = await page.evaluate(js)
    except Exception as e:
        return {"error": f"evaluate: {e}"}
    if isinstance(out, list):
        out = out[0] if out else None
    if isinstance(out, dict):
        return out
    if isinstance(out, str) and out.strip():
        try:
            return json.loads(out)
        except Exception:
            return {"error": f"parse: {out[:100]}"}
    return {"error": "empty result"}


# ── Main capture loop ──────────────────────────────────────────────────────────
async def _capture_pass(page, video_ctx, captured_pages, all_raw,
                        seen_ids, out_path, video_id, job_state,
                        max_scrolls, max_comments):
    """Satu pass capture: DOM + route intercept + scroll + API fetch."""
    new = 0
    stale = 0
    STALE_LIMIT = 6

    for i in range(max_scrolls):
        # human_act: jeda acak sebelum setiap scroll (bukan bot yang pola-pola)
        await ahuman_delay(0.8, 2.2)
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
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            r = raw_from_dom(row, video_ctx)
            all_raw.append(r)
            dom_added += 1

        # Drain route-intercepted pages
        cdp_added = 0
        while captured_pages:
            pg = captured_pages.pop(0)
            for api_comment in pg.get("comments", []):
                cid = api_comment.get("cid", "")
                if cid:
                    if cid in seen_ids:
                        continue
                    fprint = str(api_comment.get("text", ""))[:120] + "|" + str((api_comment.get("user", {}) or {}).get("unique_id", ""))
                    h = 0
                    for ch in fprint:
                        h = (h * 31 + ord(ch)) & 0x7fffffff
                    dom_alias = "dom_" + _b36(h)
                    if dom_alias in seen_ids:
                        seen_ids.add(cid)
                        continue
                    seen_ids.add(cid)
                r = raw_from_api(api_comment, video_ctx, method="route",
                                     parent_comment_id=pg.get("parent_comment_id", ""))
                all_raw.append(r)
                cdp_added += 1

        # API fetch (same-origin, fallback)
        api_added = 0
        cursor = getattr(_capture_pass, "_api_cursor", 0)
        has_more = getattr(_capture_pass, "_api_has_more", True)
        if has_more:
            pg = await fetch_comments_api(page, video_id, cursor) or {}
            if pg.get("error"):
                print(f"[api] fetch err: {pg['error'][:100]}")
                has_more = False
            else:
                for api_comment in pg.get("comments", []):
                    cid = api_comment.get("cid", "")
                    if cid and cid in seen_ids:
                        continue
                    if cid:
                        seen_ids.add(cid)
                    r = raw_from_api(api_comment, video_ctx, method="api")
                    all_raw.append(r)
                    api_added += 1
                _capture_pass._api_cursor = pg.get("cursor", 0)
                _capture_pass._api_has_more = bool(pg.get("has_more", 0))
        if api_added == 0:
            _capture_pass._api_has_more = False
            _capture_pass._api_cursor = 0

        # Expand replies — RECURSIVE (max 5 rounds per iteration)
        for _expand_round in range(5):
            try:
                expand_count = await page.evaluate("""(() => {
                    var clicked = 0;
                    var selectors = [
                        '[data-e2e^="view-more-"]',
                        '[class*="ReplyActionText"]',
                        '[class*="ViewActionText"]',
                        '[data-e2e*="reply-more"]',
                    ];
                    selectors.forEach(function(sel) {
                        document.querySelectorAll(sel).forEach(function(el) {
                            if (!el.dataset._ttc) { el.click(); el.dataset._ttc = '1'; clicked++; }
                        });
                    });
                    if (clicked === 0) {
                        document.querySelectorAll('div, span, p, button').forEach(function(el) {
                            var t = (el.textContent || '').trim();
                            if (/^View \\d+ repl/i.test(t) || /^Lihat \\d+ balasan/i.test(t) ||
                                /^View all \\d+ repl/i.test(t)) {
                                if (!el.dataset._ttc) { el.click(); el.dataset._ttc = '1'; clicked++; }
                            }
                        });
                    }
                    return clicked;
                })()""")
                if isinstance(expand_count, list):
                    expand_count = expand_count[0] if expand_count else 0
                if expand_count and expand_count > 0:
                    await asyncio.sleep(2)
                else:
                    break
            except Exception:
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

        # Write JSONL incrementally
        records = [r.to_dict() for r in all_raw]
        written = write_jsonl(str(out_path), records)
        job_state["comments_seen"] = len(seen_ids)
        job_state["comments_written"] = written
        job_state["cursor"] = i
        save_job(video_id, job_state)

        iter_new = dom_added + cdp_added + api_added
        print(f"[COLLECT] iteration={i+1} visible={visible} dom=+{dom_added} route=+{cdp_added} api=+{api_added} total={len(all_raw)}")
        new += iter_new
        if iter_new == 0:
            stale += 1
        else:
            stale = 0

        if len(all_raw) >= max_comments:
            print(f"[collector] Cap {max_comments} komentar. Stop.")
            break
        if stale >= STALE_LIMIT and i >= 10:
            print(f"[collector] stale stop at iter {i+1} (no new unique IDs for {STALE_LIMIT} iters)")
            break

        try:
            await page.evaluate(SCROLL_JS)
        except Exception:
            pass
        await asyncio.sleep(2)
    return new


# ── Main entry point ───────────────────────────────────────────────────────────
async def collect_video(
    video_url: str,
    max_scrolls: int = 60,
    max_comments: int = 300,
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
        print(f"[collector] Resume job: cursor={job_state.get('cursor')}, seen={job_state.get('comments_seen')}")
    else:
        job_state = {"job_id": resume or f"job_{video_id}_{int(time.time())}", "video_id": video_id, "cursor": 0, "comments_seen": 0, "comments_written": 0, "status": "running"}
        job_state["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    out_path = raw_path(video_id)
    all_raw: List[RawComment] = []
    seen_ids: set = set()
    captured_pages: list = []
    route_counters = {"observed": 0, "comment_like": 0, "parsed_ok": 0, "comments": 0}

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
                return {"error": "blocked", "status": "blocked", "captcha": res}
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
            click_result = "not found"
            for attempt in range(5):
                try:
                    click_result = await page.evaluate(CLICK_COMMENT_PANEL_JS)
                except Exception:
                    click_result = "eval error"
                print(f"[collector] open panel: {click_result}")
                if click_result != "not found":
                    break
                await asyncio.sleep(2)
            if click_result == "not found":
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

            pass_new = await _capture_pass(page, video_ctx, captured_pages, all_raw,
                                           seen_ids, out_path, video_id, job_state,
                                           max_scrolls, max_comments)
            if pass_new > 0 or pass_new == "blocked":
                break

        # Drain any remaining intercepted pages (single drain, no duplicate)
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

        write_jsonl(str(out_path), [r.to_dict() for r in all_raw])

        # Collection completeness
        captured = len(all_raw)
        if reported_count > 0:
            coverage = round(captured / reported_count, 3)
            collection_status = "complete" if coverage >= 1.0 else "partial"
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
    }


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
    parser.add_argument("--scrolls", type=int, default=60, help="Max scroll iterations")
    parser.add_argument("--max", type=int, default=300, help="Max comments")
    parser.add_argument("--resume", help="Resume job ID (video_id)")
    parser.add_argument("--login", action="store_true", help="Login only")
    parser.add_argument("--camoufox", action="store_true", help="Force Camoufox (skip CDP detection)")
    parser.add_argument("--detect", action="store_true", help="Detect running browsers via CDP")
    args = parser.parse_args()

    if args.detect:
        from src.browser_selector import _test_detect
        asyncio.run(_test_detect())
    elif args.login or not args.url:
        asyncio.run(login_only())
    else:
        for u in args.url:
            print(f"\n########## COLLECT: {u} ##########\n")
            asyncio.run(collect_video(
                video_url=u,
                max_scrolls=args.scrolls,
                max_comments=args.max,
                resume=args.resume,
                force_camoufox=args.camoufox,
            ))


if __name__ == "__main__":
    main()
