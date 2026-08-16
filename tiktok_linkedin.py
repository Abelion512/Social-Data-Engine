#!/usr/bin/env python3
"""
TikTok Comment → LinkedIn Connect Pipeline
CDP network capture via nodriver + linkedin-cli.

Usage:
  python tiktok_linkedin.py                           # Login only (first run)
  python tiktok_linkedin.py <tiktok_url>              # Capture + match
  python tiktok_linkedin.py <tiktok_url> --connect    # Auto-connect
  python tiktok_linkedin.py <tiktok_url> --max 50     # Limit comments
"""

import nodriver as uc
# nodriver network import no longer needed — using JS fetch/XHR intercept
import json
import asyncio
import sys
import time
import os
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Optional

# ── Config ──────────────────────────────────────────────────────────────────
PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"
DEEPSEEK_API = "http://localhost:20128/v1/chat/completions"  # 9Router proxy
DEEPSEEK_MODEL = "oc/deepseek-v4-flash-free"  # 3s vs 20s (mimo) — cukup untuk enrich
ENRICH_MODELS = ["kc/nvidia/nemotron-3-super-120b-a12b:free", "MARK", "abelink", DEEPSEEK_MODEL]  # nemotron primary (kualitas), combo fallback cepat
VISION_MODEL = "gc/gemini-3.1-flash-lite-preview"  # 9Router Vision Adapter (OCR foto)
VISION_MODEL_FALLBACK = "oc/mimo-v2.5-free"  # second vision-capable model

LINKEDIN_LIMITS = {
    "max_connections_per_day": 20,
    "min_delay_between_ms": 30,
}

def daily_conn_count() -> int:
    """Sends this rolling day. Persisted so multi-run days don't overshoot."""
    today = time.strftime("%Y-%m-%d")
    cnt = Path(STATE_DIR / "daily_conn.json")
    data = {}
    if cnt.exists():
        try:
            data = json.loads(cnt.read_text())
        except Exception:
            data = {}
    return data.get(today, 0)


def bump_daily_conn_count(n: int = 1) -> int:
    today = time.strftime("%Y-%m-%d")
    cnt = Path(STATE_DIR / "daily_conn.json")
    data = {}
    if cnt.exists():
        try:
            data = json.loads(cnt.read_text())
        except Exception:
            data = {}
    total = data.get(today, 0) + n
    data[today] = total
    cnt.write_text(json.dumps(data, indent=2))
    return total

LINKEDIN_URL_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9_-]+)")

def already_connected(handle: str) -> bool:
    """True if any past connections file recorded Connected/Pending for this handle.

    Pending counts too: the invite was already sent — re-fetching the profile
    and re-sending would spam the target and waste ~70s per handle."""
    for f in sorted(Path(STATE_DIR).glob("connections_*.json")):
        try:
            for c in json.loads(f.read_text()):
                r = c.get("result") or {}
                if c.get("profile") == handle and r.get("state") in ("Connected", "Pending"):
                    return True
        except Exception:
            continue
    return False


# ── Helpers ─────────────────────────────────────────────────────────────────
def linkedin_env() -> Dict[str, str]:
    """Subprocess env for linkedin-cli. Hermes gateway leaks PYTHONPATH (greenlet
    3.11 vs venv 3.12 crash) and may drop DISPLAY/XAUTHORITY → playwright can't
    open X. Strip both explicitly."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
    return env


def ensure_dirs():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

def save_state(name, data):
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = STATE_DIR / f"{name}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[save] {path}")
    return path


# ── P0: TikTok CDP Capture ──────────────────────────────────────────────────
async def init_browser(headless=False):
    """Launch Chrome with persistent profile."""
    ensure_dirs()
    browser = await uc.start(
        headless=headless,
        user_data_dir=str(PROFILE_DIR),
    )
    # Enable CDP network + runtime for response interception
    try:
        import nodriver.cdp.network as net
        import nodriver.cdp.runtime as runtime
        tab = browser.main_tab
        await tab.send(net.enable())
        await tab.send(runtime.enable())
        print("[init] CDP network monitoring enabled")
    except Exception as e:
        print(f"[init] CDP enable failed: {e}")
    return browser


async def capture_comments(browser, video_url: str, max_scrolls: int = 20, expand_replies: bool = True) -> List[Dict]:
    """
    Capture TikTok comments — DOM-primary. TikTok SSR-preloads comments into
    the DOM (no fetch API in most cases), so: open comment panel, poll DOM for
    comment elements, scroll+re-scrape. CDP network handler kept as supplement.
    """
    import nodriver.cdp.network as cdp_network
    import base64

    video_url = video_url.replace("\\", "")
    tab = browser.main_tab

    captured_comment_pages: list[dict] = []
    captured_reply_pages: list[dict] = []

    async def _on_response(event: cdp_network.ResponseReceived):
        url = event.response.url or ""
        if "comment" not in url:
            return
        is_reply = "/reply/" in url or "comment/list/reply" in url
        try:
            body_str, is_b64 = await tab.send(cdp_network.get_response_body(event.request_id))
            if is_b64:
                body_str = base64.b64decode(body_str).decode("utf-8", errors="replace")
            data = json.loads(body_str)
        except Exception:
            return
        if data.get("status_code", -1) != 0:
            return
        page = {
            "comments": data.get("comments") or [],
            "has_more": data.get("has_more", 0),
            "cursor": data.get("cursor", 0),
        }
        if is_reply:
            captured_reply_pages.append(page)
            print(f"[cdp] reply page: +{len(page['comments'])}")
        else:
            captured_comment_pages.append(page)
            print(f"[cdp] comment page: +{len(page['comments'])}")

    tab.add_handler(cdp_network.ResponseReceived, _on_response)
    print("[capture] CDP handler registered")

    # Probe: log all comment-ish API URLs seen (diagnose endpoint drift)
    async def _on_url_probe(event: cdp_network.RequestWillBeSent):
        u = event.request.url or ""
        if "comment" in u.lower() or "/aweme/" in u.lower():
            print(f"[net] {u[:160]}")
    tab.add_handler(cdp_network.RequestWillBeSent, _on_url_probe)

    # ── Navigate (with retry) ──────────────────────────────────────
    click_js = """(function() {
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

    # "View all N comments" button (older layout) — clicking it also opens panel
    view_all_js = """(() => {
        var els = document.querySelectorAll('div[role="button"], span, p, button');
        for (var i = 0; i < els.length; i++) {
            var t = (els[i].textContent || '').trim();
            if (/^View (all )?\\d+ comments?$/i.test(t) || /^Lihat (semua )?\\d+ komentar/i.test(t)) {
                els[i].click(); return 'clicked view-all: ' + t.slice(0, 40);
            }
        }
        return 'none';
    })()"""

    dom_scrape_js = """(() => {
        const out = [];
        const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
        items.forEach(el => {
            const raw = el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
            const imgs = Array.from(el.querySelectorAll('img[src*="tiktokcdn"], img[src^="http"]'))
                .map(i => i.src).filter(s => s && !s.includes('static/') && !s.includes('avatar')).slice(0, 4);
            if ((!raw || raw.length < 3) && imgs.length === 0) return;
            // username lives in the same comment-item wrapper, not in the text span
            const wrapper = el.closest('[class*="DivCommentObjectWrapper"], [data-e2e^="comment-item-"]') || el.parentElement;
            let uname = '';
            if (wrapper) {
                const a = wrapper.querySelector('a[href*="/@"]');
                uname = a ? a.getAttribute('href').replace(/^\\/@/, '').split('?')[0] : '';
            }
            if (!uname) {
                const nu = document.querySelector('[data-e2e*="comment-username-"]');
                uname = nu ? nu.textContent.trim().replace(/^@/, '') : '';
            }
            out.push({
                raw: raw.substring(0, 400),
                username: uname || raw.split(' ')[0],
                images: imgs,
            });
        });
        const seen = new Set();
        return JSON.stringify(out.filter(o => (seen.has(o.raw + '|' + o.images.join(',')) ? false : (seen.add(o.raw + '|' + o.images.join(',')), true))));
    })()"""

    scroll_js = """(() => {
        var list = document.querySelector('[data-e2e="comment-list"]');
        var cont = null;
        if (list) {
            var p = list.parentElement;
            while (p && p.scrollHeight <= p.clientHeight + 10) p = p.parentElement;
            if (p) cont = p;
        }
        if (!cont) cont = document.querySelector('[class*="DivCommentListContainer"]');
        if (!cont) { window.scrollBy(0, 800); return; }
        // click any load-more / pagination trigger visible
        document.querySelectorAll('[data-e2e*="load-more"], [class*="LoadMore"], [data-e2e*="comment-load"]').forEach(el => { el.click(); });
        // jump to bottom — this is what triggers TikTok's lazy-load
        var before = cont.scrollTop;
        cont.scrollTop = cont.scrollHeight;
        if (cont.scrollTop === before && cont.scrollTop > 0) {
            // already at bottom: bounce to top then bottom again to re-trigger
            cont.scrollTop = 0;
            setTimeout(() => { cont.scrollTop = cont.scrollHeight; }, 150);
        }
    })()"""

    comments: List[Dict] = []
    seen_raw = set()

    def _collect_from_dom(rows):
        nonlocal seen_raw, comments
        n = 0
        for r in rows:
            raw = (r.get("raw") or "").strip()
            imgs = r.get("images") or []
            key = raw if raw else (r.get("username", "") + "::" + "|".join(imgs))
            if not key or key in seen_raw:
                continue
            seen_raw.add(key)
            comments.append({
                "username": r.get("username", ""), "display_name": "",
                "comment_text": raw,
                "images": imgs,
                "likes": 0, "replies": 0, "comment_id": "", "create_time": 0,
            })
            n += 1
        return n

    for nav_attempt in range(3):
        if nav_attempt > 0:
            print(f"[capture] retry nav {nav_attempt + 1}...")
            await tab.get(video_url)
            await asyncio.sleep(6)

        print(f"[capture] Navigating to: {video_url}")
        if nav_attempt == 0:
            await tab.get(video_url)
            await asyncio.sleep(6)

        current_url = await tab.evaluate("window.location.href")
        print(f"[capture] URL: {current_url}")
        if "login" in current_url.lower() or "verify" in current_url.lower():
            print("[!] Redirected (login/verify)")
            return []

        # Open comment panel (retry — element appears async)
        click_result = "not found"
        for attempt in range(5):
            try:
                click_result = await tab.evaluate(click_js)
            except Exception:
                click_result = "eval error"
            print(f"[capture] click[{attempt+1}] {click_result}")
            if click_result != "not found":
                break
            await asyncio.sleep(3)

        # Try "View all comments" if panel didn't open
        if click_result == "not found":
            va = await tab.evaluate(view_all_js)
            print(f"[capture] view-all: {va}")
            await asyncio.sleep(2)

        # Wait for skeletons to resolve into real comments (API round-trip)
        print("[capture] waiting for comment content (skeletons → data)...")
        for _ in range(15):
            try:
                skel = await tab.evaluate(
                    "document.querySelectorAll('.TUXSkeletonRectangle').length")
                levels = await tab.evaluate(
                    "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length")
                if levels > 0:
                    break
                if isinstance(skel, (int, float)) and skel == 0 and levels == 0:
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        # CAPTCHA check
        body_txt = await tab.evaluate("document.body ? document.body.innerText.slice(0,3000) : ''")
        if "Verify" in body_txt and "human" in body_txt:
            print("[!] TikTok blocked (captcha)")
            return []

        # Scroll + collect loop
        stale = 0
        for i in range(max_scrolls):
            try:
                dom_str = await tab.evaluate(dom_scrape_js)
                rows = json.loads(dom_str) if isinstance(dom_str, str) else (dom_str or [])
            except Exception:
                rows = []

            n = _collect_from_dom(rows)
            # drain CDP pages too
            while captured_comment_pages:
                page = captured_comment_pages.pop(0)
                for c in _parse_comment_items(page.get("comments", [])):
                    key = c.get("comment_text", "")[:60]
                    if key not in seen_raw:
                        seen_raw.add(key)
                        comments.append(c)
            if n:
                print(f"[capture] iter {i+1}: +{n} (total {len(comments)})")
                stale = 0
            else:
                stale += 1

            if stale >= 6 and i >= 10:
                print(f"[capture] stale stop at iter {i+1}")
                break

            try:
                await tab.evaluate(scroll_js)
            except Exception:
                pass
            await asyncio.sleep(2.5)

        if comments:
            break
        print(f"[capture] attempt {nav_attempt+1} got 0 — will retry" if nav_attempt < 2 else "[capture] all attempts exhausted")

    # Phase 2: Expand replies
    if expand_replies:
        expand_js = """(() => {
            var clicked = 0;
            document.querySelectorAll('[data-e2e^="view-more-"], [class*="ReplyActionText"]').forEach(el => {
                if (!el.dataset._ttc) { el.click(); el.dataset._ttc = '1'; clicked++; }
            });
            return clicked;
        })()"""
        for _ in range(3):
            try:
                n = await tab.evaluate(expand_js)
                if n:
                    print(f"[capture] replies expanders: {n}")
            except Exception:
                pass
            await asyncio.sleep(2)
            try:
                dom_str = await tab.evaluate(dom_scrape_js)
                rows = json.loads(dom_str) if isinstance(dom_str, str) else (dom_str or [])
                _collect_from_dom(rows)
            except Exception:
                pass

    for page in captured_reply_pages:
        for c in page.get("comments", []):
            comments.append(_reply_to_dict(c, c.get("cid", "")))

    if not comments:
        try:
            html = await tab.evaluate("document.documentElement.outerHTML")
            p = Path.home() / ".tiktok-linkedin" / "state" / "debug_page.html"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html[:300000], encoding="utf-8")
            print(f"[capture] Debug HTML saved: {p}")
        except Exception:
            pass

    print(f"[capture] Raw comments: {len(comments)}")
    return comments


def _comment_to_dict(c: dict) -> Dict:
    """Convert a TikTok comment API item to our schema."""
    user = c.get("user", {}) or {}
    return {
        "username": user.get("unique_id", ""),
        "display_name": user.get("nickname", ""),
        "comment_text": c.get("text", ""),
        "likes": c.get("digg_count", 0),
        "replies": c.get("reply_comment_total", 0),
        "comment_id": c.get("cid", ""),
        "create_time": c.get("create_time", 0),
    }


def _reply_to_dict(reply: dict, parent_id: str) -> Dict:
    """Convert a TikTok reply item to our schema."""
    user = reply.get("user", {}) or {}
    return {
        "username": user.get("unique_id", ""),
        "display_name": user.get("nickname", ""),
        "comment_text": reply.get("text", ""),
        "likes": reply.get("digg_count", 0),
        "replies": 0,
        "comment_id": reply.get("cid", ""),
        "create_time": reply.get("create_time", 0),
        "is_reply": True,
        "parent_id": parent_id,
    }


def _parse_comment_items(items: List[dict]) -> List[Dict]:
    """Flatten comments + replies into our schema."""
    out = []
    for c in items:
        out.append(_comment_to_dict(c))
        for reply in (c.get("reply_comment") or []):
            out.append(_reply_to_dict(reply, c.get("cid", "")))
    return out


def _extract_comments_from_ssr(data: dict) -> List[Dict]:
    """Recursively search SSR JSON for comment data."""
    comments = []

    # Try known paths
    # Path 1: props.pageProps.commentList
    try:
        page_props = data.get("props", {}).get("pageProps", {})
        for key in ["commentList", "comments", "itemStruct"]:
            val = page_props.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                if "text" in val[0] or "comment_text" in val[0]:
                    for c in val:
                        comments.append({
                            "username": c.get("user", {}).get("unique_id", c.get("username", "")),
                            "display_name": c.get("user", {}).get("nickname", c.get("display_name", "")),
                            "comment_text": c.get("text", c.get("comment_text", "")),
                            "likes": c.get("digg_count", c.get("likes", 0)),
                            "replies": c.get("reply_comment_total", 0),
                            "comment_id": c.get("cid", c.get("comment_id", "")),
                            "create_time": c.get("create_time", 0),
                        })
                    if comments:
                        return comments
    except Exception:
        pass

    # Path 2: ItemModule > videoId > commentItems
    try:
        item_module = data.get("ItemModule", {})
        for video_id, video_data in item_module.items():
            comment_items = video_data.get("commentItems", [])
            for c in comment_items:
                comments.append({
                    "username": c.get("user", {}).get("unique_id", ""),
                    "display_name": c.get("user", {}).get("nickname", ""),
                    "comment_text": c.get("text", ""),
                    "likes": c.get("digg_count", 0),
                    "replies": c.get("reply_comment_total", 0),
                    "comment_id": c.get("cid", ""),
                    "create_time": c.get("create_time", 0),
                })
            if comments:
                return comments
    except Exception:
        pass

    # Path 3: Deep search for "comments" key anywhere
    def _deep_search(obj, depth=0):
        if depth > 10 or len(comments) > 0:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("comments", "commentList", "commentItems") and isinstance(v, list):
                    for c in v:
                        if isinstance(c, dict) and "text" in c:
                            comments.append({
                                "username": c.get("user", {}).get("unique_id", ""),
                                "display_name": c.get("user", {}).get("nickname", ""),
                                "comment_text": c.get("text", ""),
                                "likes": c.get("digg_count", 0),
                                "replies": c.get("reply_comment_total", 0),
                                "comment_id": c.get("cid", ""),
                                "create_time": c.get("create_time", 0),
                            })
                    if comments:
                        return
                _deep_search(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:  # limit to prevent infinite recursion
                _deep_search(item, depth + 1)

    if not comments:
        _deep_search(data)

    return comments


def deduplicate_comments(comments: List[Dict]) -> List[Dict]:
    seen = set()
    unique = []
    for c in comments:
        text = c.get("comment_text", "") or ""
        imgs = c.get("images") or []
        key = f"{c['username']}::{text[:60]}"
        if not text.strip():
            key = f"{c['username']}::img:{'|'.join(imgs)}"
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ── P1: Enrich via DeepSeek ──────────────────────────────────────────────────
def extract_identities(comments: List[Dict], api_key: str) -> List[Optional[Dict]]:
    """Use DeepSeek V4 Flash to infer real names + companies from comments."""
    import requests

    identities: List[Optional[Dict]] = []
    # chunk to keep responses fast; 10 comments max per call
    BATCH = 10
    for start in range(0, len(comments), BATCH):
        chunk = comments[start:start + BATCH]
        batch = [{
            "username": c["username"],
            "display_name": c["display_name"],
            "comment": c["comment_text"][:200],
        } for c in chunk]

        prompt = f"""Analyze TikTok commenters. Infer real identity.

For each:
- real_name: actual name (from display_name or context). Only if display_name looks like a real name (e.g. "John Smith"), not usernames like "funny_cat_42"
- company: employer/company if mentioned or inferable
- role: job title if mentioned
- linkedin_hint: best search query for LinkedIn
- confidence: 0-1

Commenters:
{json.dumps(batch, indent=2, ensure_ascii=False)}

Return JSON array. null if not inferable."""

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # try model chain in order; fall through on error/empty/parse-fail
        content = None
        last_err = None
        for model in ENRICH_MODELS:
            for attempt in (1, 2):
                try:
                    resp = requests.post(DEEPSEEK_API, json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2048,
                        "temperature": 0.1,
                        "stream": False,
                    }, headers=headers, timeout=120)
                    result = resp.json()
                    if "error" in result or "choices" not in result:
                        last_err = f"{model} (try {attempt}): {str(result.get('error', result.keys()))[:150]}"
                        print(f"[!] enrich {last_err}")
                        time.sleep(3 * attempt)
                        continue
                    content = result["choices"][0]["message"]["content"]
                    if content and content.strip():
                        break
                    last_err = f"{model} (try {attempt}): empty content"
                    print(f"[!] enrich {last_err}")
                except Exception as e:
                    last_err = f"{model} (try {attempt}): {type(e).__name__}: {e}"
                    print(f"[!] enrich {last_err}")
                    time.sleep(3 * attempt)
            if content and content.strip():
                break
        if not content or not content.strip():
            print(f"[!] enrich all models failed; last: {last_err}")
            identities.extend([None] * len(chunk))
            continue
        # Extract JSON from markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        try:
            parsed = json.loads(content.strip())
            if not isinstance(parsed, list):
                parsed = [parsed]
            identities.extend(parsed[:len(chunk)])
        except json.JSONDecodeError as e:
            print(f"[!] enrich parse fail: {e}; content={content[:300]!r}")
            identities.extend([None] * len(chunk))
    return identities


def extract_handle_from_image(image_url: str, api_key: str) -> Optional[str]:
    """OCR LinkedIn handle from a comment photo via 9Router Vision Adapter.
    Sends the image URL as an OpenAI-style image_url content part; 9Router
    proxies vision for non-vision models automatically.
    Robust: falls back to VISION_MODEL_FALLBACK on adapter error, and to
    regex extraction when the model answers free text instead of JSON."""
    import requests

    prompt = (
        "This image was posted in a TikTok comment section. It may contain a LinkedIn "
        "profile URL, QR code, or handle like https://www.linkedin.com/in/john-doe-123.\n"
        "Extract ONLY the LinkedIn public identifier (the part after linkedin.com/in/).\n"
        "Rules:\n"
        "- If you see a LinkedIn URL like linkedin.com/in/username -> return 'username'\n"
        "- If you see text like 'linkedin: johndoe' or '@johndoe' -> return 'johndoe'\n"
        "- If the image is a QR code or contains no LinkedIn identifier -> return null\n"
        "Answer with a single JSON object: {\"handle\": \"username\" or null}"
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _call(model: str) -> Optional[str]:
        resp = requests.post(DEEPSEEK_API, json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "max_tokens": 100,
            "temperature": 0.1,
            "stream": False,
        }, headers=headers, timeout=120)
        result = resp.json()
        if "error" in result:
            print(f"[vision] {model} error: {result['error']}")
            return None
        return result.get("choices", [{}])[0].get("message", {}).get("content", "")

    for model in (VISION_MODEL, VISION_MODEL_FALLBACK):
        try:
            content = _call(model)
            if not content:
                continue
            # JSON path
            blob = content
            if "```json" in blob:
                blob = blob.split("```json")[1].split("```")[0]
            elif "```" in blob:
                blob = blob.split("```")[1].split("```")[0]
            try:
                parsed = json.loads(blob.strip())
                handle = parsed.get("handle") if isinstance(parsed, dict) else None
                if isinstance(handle, str) and handle.strip() and handle.strip().lower() != "null":
                    return handle.strip()
            except json.JSONDecodeError:
                pass
            # Free-text fallback: regex for linkedin.com/in/<handle>
            m = LINKEDIN_URL_RE.search(content)
            if m:
                return m.group(1).rstrip("/?")
            # Plain "johndoe" style single token
            tok = content.strip().strip('"\'.,;: ')
            if tok and tok.lower() not in ("null", "none", "no", "not found", "no handle", "-"):
                if len(tok) <= 80 and " " not in tok:
                    return tok
        except Exception as e:
            print(f"[vision] extract handle err ({model}): {e}")
    return None


# ── P2: LinkedIn ────────────────────────────────────────────────────────────
def search_linkedin(name: str, company: str = None) -> List[Dict]:
    """Search LinkedIn via linkedin-cli (darwincr/linkedin-cli)."""
    query = f"{name} {company or ''}".strip()
    try:
        result = subprocess.run(
            ["linkedin-cli", "search", query, "--limit", "3", "--json"],
            capture_output=True, text=True, timeout=60, env=linkedin_env(),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("profiles", [])
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[linkedin] search err: {e}")
    return []


def send_connect(handle: str, note: Optional[str] = None) -> Optional[Dict]:
    """Send LinkedIn connection request (darwincr fork: always no-note).
    Retries transient failures; 403 privacy blocks are permanent (no retry)."""
    cmd = ["linkedin-cli", "connect", handle, "--json"]
    last_err = None
    for attempt in range(1, 4):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=100, env=linkedin_env())
            if result.returncode == 0:
                return json.loads(result.stdout)
            err = result.stderr.strip().splitlines()
            last_err = err[-1] if err else f"rc={result.returncode}"
            print(f"[linkedin] connect {handle} (try {attempt}): {last_err}")
            # Permanent: privacy-blocked profile. No retry.
            if "profile_inaccessible" in last_err or "HTTP 403" in last_err:
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            last_err = str(e)
            print(f"[linkedin] connect {handle} (try {attempt}): {e}")
            if isinstance(e, (FileNotFoundError, json.JSONDecodeError)):
                return None
        # Backoff between retries (transient: skip_profile, 429, network)
        if attempt < 3:
            time.sleep(attempt * 5)
    print(f"[linkedin] connect {handle}: giving up after 3 tries ({last_err})")
    return None


def fetch_profile(handle: str) -> Optional[Dict]:
    """Fetch profile by handle (ground truth from direct link)."""
    try:
        result = subprocess.run(
            ["linkedin-cli", "profile", handle, "--json"],
            capture_output=True, text=True, timeout=60, env=linkedin_env(),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("public_identifier"):
                return data
        err = result.stderr.strip().splitlines()
        print(f"[linkedin] profile {handle}: {err[-1] if err else f'rc={result.returncode}'}")
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[linkedin] profile err: {e}")
    return None


def generate_note(profile: Dict, comment: Dict) -> str:
    name = profile.get("full_name", "there")
    return f"Hi {name}! Noticed your comment on TikTok — thought we should connect."


def cleanse_comments(comments: List[Dict]) -> List[Dict]:
    """Drop junk rows: empty text, pure-emoji, spammy URLs, unbalanced quotes."""
    cleaned = []
    for c in comments:
        text = (c.get("comment_text") or "").strip()
        imgs = c.get("images") or []
        # keep image-only comments (handles may be in the photo) — cleansed later via OCR
        if len(text) < 3 and not imgs:
            continue
        # Drop comment that is basically "linking" stub from TikTok UI text
        if text.lower().startswith(("linking", "add comment")):
            continue
        if text.count("http") > 1:
            continue
        # keep username if present, else fallback display_name
        if not c.get("username") and c.get("display_name"):
            c["username"] = c["display_name"]
        cleaned.append(c)
    return cleaned


def verify_names(comments: List[Dict]) -> Dict:
    """Check extracted names are plausible (>=2 chars, no digits-only, sane ratio)."""
    ok = 0
    issues = []
    for c in comments:
        identity = c.get("identity") or {}
        name = identity.get("real_name", "")
        if not name:
            issues.append((c.get("username", "?"), "no real_name"))
            continue
        if len(name) < 2 or not any(ch.isalpha() for ch in name):
            issues.append((c.get("username", "?"), f"bad name: {name!r}"))
            continue
        ok += 1
    return {"verified": ok, "total": len(comments), "issues": issues[:10]}


def write_csv_report(comments: List[Dict], matches: List[Dict], connections: List[Dict]) -> Path:
    """CSV with one row per comment: scraped/enriched/matched/connected status."""
    import csv as _csv

    conn_map = {conn.get("profile"): conn.get("result") for conn in connections}
    match_by_name = {}
    match_by_user = {}
    for m in matches:
        identity = m.get("identity") or {}
        match_by_name[identity.get("real_name", "")] = m.get("linkedin", {})
        match_by_user[m.get("comment", {}).get("username", "")] = m.get("linkedin", {})

    ts = time.strftime("%Y%m%d_%H%M%S")
    rows = []
    for i, c in enumerate(comments, 1):
        identity = c.get("identity") or {}
        name = identity.get("real_name", "")
        profile = match_by_user.get(c.get("username", "")) or match_by_name.get(name, {})
        handle = profile.get("public_identifier") or profile.get("handle", "")
        if handle in conn_map:
            r = conn_map[handle]
            state = (r or {}).get("state", "Pending")
            connected = "YES" if state == "Connected" else ("FAILED" if r is None else "PENDING")
        else:
            connected = "NO"
        profile_url = f"https://www.linkedin.com/in/{handle}" if handle else ""
        rows.append([
            i, c.get("username", ""), c.get("display_name", ""), name,
            identity.get("company", ""), c.get("comment_text", ""),
            "YES", "YES" if name else "NO",
            "YES" if profile else "NO", profile_url, connected,
            "no-note" if handle and handle in conn_map else "",
        ])

    header = [
        "no", "tiktok_username", "display_name", "real_name", "company",
        "comment_text", "scraped", "name_verified", "linkedin_match",
        "linkedin_url", "connected", "note",
    ]
    # Two files: already-connected vs not-yet (Pending/FAILED/NO)
    base = STATE_DIR / f"report_{ts}"
    for suffix, pred in (("_connected.csv", lambda r: r[10] == "YES"),
                         ("_pending.csv", lambda r: r[10] != "YES")):
        path = Path(str(base) + suffix)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(header)
            for r in rows:
                if pred(r):
                    w.writerow(r)
        print(f"[csv] {path.name}: {sum(1 for r in rows if pred(r))} rows")
    return base


# ── P3: Pipeline Orchestrator ───────────────────────────────────────────────
async def run_pipeline(
    video_url: str,
    deepseek_key: str,
    auto_connect: bool = False,
    max_scrolls: int = 20,
    max_comments: int = 100,
    no_note: bool = False,
):
    """Full pipeline: TikTok → LinkedIn."""

    browser = await init_browser()

    # ── Step 1: Capture ──
    print(f"\n{'='*60}")
    print("  STEP 1: Capturing TikTok comments")
    print(f"{'='*60}\n")

    raw_comments = await capture_comments(browser, video_url, max_scrolls)
    comments = deduplicate_comments(raw_comments)
    for i, c in enumerate(comments[:3]):
        print(f"[dbg] pre-clean user={c.get('username')!r} text={c.get('comment_text','')[:80]!r}")
    comments = cleanse_comments(comments)
    comments = comments[:max_comments]
    print(f"[dedup+cleanse] {len(raw_comments)} → {len(comments)} unique")

    if not comments:
        print("[!] No comments captured. Try more scrolls or check video.")
        browser.stop()
        return []

    save_state("comments", comments)

    # ── Step 2: Enrich ──
    print(f"\n{'='*60}")
    print("  STEP 2: Extracting identities (DeepSeek)")
    print(f"{'='*60}\n")

    try:
        identities = extract_identities(comments, deepseek_key)
    except Exception as e:
        print(f"[!] DeepSeek failed: {e}")
        identities = []
    # Pad identities to match comments length
    if len(identities) < len(comments):
        identities.extend([None] * (len(comments) - len(identities)))

    for i, comment in enumerate(comments):
        comment["identity"] = identities[i] if i < len(identities) else None

    with_identity = [c for c in comments if (c.get("identity") or {}).get("real_name")]
    print(f"[enrich] {len(with_identity)} comments with identifiable names")

    # Name verification
    v = verify_names(comments)
    print(f"[verify] {v['verified']}/{v['total']} names plausible")
    if v["issues"]:
        for user, why in v["issues"]:
            print(f"  ⚠ {user}: {why}")

    save_state("enriched", with_identity)

    # ── Step 3: LinkedIn Match ──
    print(f"\n{'='*60}")
    print("  STEP 3: Searching LinkedIn")
    print(f"{'='*60}\n")

    matches = []
    matched_users = set()

    # Layer A: direct LinkedIn links (ground truth — skip guessing entirely)
    for c in comments:
        m = LINKEDIN_URL_RE.search(c.get("comment_text", "") or "")
        if not m:
            continue
        handle = m.group(1).rstrip("/?")
        profile = fetch_profile(handle)
        if profile:
            matches.append({"comment": c, "identity": None, "linkedin": profile, "via": "link"})
            matched_users.add(c.get("username"))
            print(f"  ✓ [link] {handle} → {profile.get('full_name', '?')}")
        else:
            print(f"  ✗ [link] {handle} → no profile")

    # Layer A2: comment PHOTOS → vision OCR (handles posted as images)
    photo_comments = [c for c in comments if c.get("images") and c.get("username") not in matched_users]
    for c in photo_comments:
        handle = None
        for img in c["images"][:3]:
            print(f"  👁 [photo] {c.get('username')} → OCR {img[:80]}...")
            handle = extract_handle_from_image(img, deepseek_key)
            if handle:
                break
        if not handle:
            print(f"  ✗ [photo] {c.get('username')} → no handle in image")
            continue
        profile = fetch_profile(handle)
        if profile:
            matches.append({"comment": c, "identity": None, "linkedin": profile, "via": "photo"})
            matched_users.add(c.get("username"))
            print(f"  ✓ [photo] {handle} → {profile.get('full_name', '?')}")
        else:
            print(f"  ✗ [photo] {handle} → no profile")

    # Layer B: name-based search (fallback for the rest)
    for c in with_identity:
        if c.get("username") in matched_users:
            continue
        identity = c["identity"]
        name = identity.get("real_name")
        company = identity.get("company")

        if not name:
            continue

        profiles = search_linkedin(name, company)
        if profiles:
            matches.append({
                "comment": c,
                "identity": identity,
                "linkedin": profiles[0],
            })
            print(f"  ✓ {name} → {profiles[0].get('full_name', '?')}")
        else:
            print(f"  ✗ {name} → no match")

        await asyncio.sleep(1)

    print(f"\n[match] {len(matches)} LinkedIn matches")
    save_state("matches", matches)

    # ── Step 4: Connect ──
    connections = []
    if auto_connect and matches:
        print(f"\n{'='*60}")
        print("  STEP 4: Sending connection requests")
        print(f"{'='*60}\n")

        consecutive_fail = 0
        for i, match in enumerate(matches):
            if i >= LINKEDIN_LIMITS["max_connections_per_day"]:
                print(f"[!] Daily limit ({LINKEDIN_LIMITS['max_connections_per_day']})")
                break

            if daily_conn_count() >= LINKEDIN_LIMITS["max_connections_per_day"]:
                print(f"[!] Daily limit reached (persisted: {daily_conn_count()}). Stop connecting; remaining matches saved.")
                break

            profile = match["linkedin"]
            handle = profile.get("public_identifier") or profile.get("handle")
            if not handle:
                print(f"  ✗ {profile.get('full_name')} — no handle")
                continue

            if already_connected(handle):
                print(f"  ⏭ {profile.get('full_name')} — already connected, skip")
                continue

            note = None if no_note else generate_note(profile, match["comment"])
            result = send_connect(handle, note)

            if result:
                consecutive_fail = 0
                connections.append({"profile": handle, "result": result})
                bump_daily_conn_count()
                state = result.get("state", "?")
                print(f"  {'✓' if state == 'Connected' else '⏳'} {state}: {profile.get('full_name')}" + (" (no-note)" if no_note else ""))
            else:
                consecutive_fail += 1
                connections.append({"profile": handle, "result": None})
                print(f"  ✗ Failed: {profile.get('full_name')}")

            delay = LINKEDIN_LIMITS["min_delay_between_ms"]
            print(f"  ⏳ {delay}s...")
            await asyncio.sleep(delay)

            # Circuit breaker: 3+ consecutive fails w/ same pattern = restriction/block.
            # Stop burning daily quota on a dead envelope.
            if consecutive_fail >= 3:
                print(f"[!] {consecutive_fail} consecutive failures — likely LinkedIn restriction/block. Stopping connect phase; {len(matches)-i-1} matches deferred to next run.")
                break

        save_state("connections", connections)
        print(f"\n[done] {len(connections)} requests sent")
    else:
        print(f"\n[review] Run with --connect to send requests.")

    # ── Step 5: CSV Report ──
    report = write_csv_report(comments, matches, connections)
    print(f"[csv] {report}")
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"  Scraped:     {len(comments)}")
    print(f"  Enriched:    {len(with_identity)}")
    print(f"  Matched:     {len(matches)}")
    n_conn = sum(1 for c in connections if (c.get("result") or {}).get("state") == "Connected")
    n_pend = sum(1 for c in connections if c.get("result") and (c.get("result") or {}).get("state") != "Connected")
    n_fail = sum(1 for c in connections if c.get("result") is None)
    print(f"  Connected:   {n_conn}")
    print(f"  Pending:     {n_pend}")
    print(f"  Failed:      {n_fail}")
    print(f"  Report:      {report}")
    print("=" * 60)

    browser.stop()
    return matches


# ── CLI ──────────────────────────────────────────────────────────────────────
async def login_only():
    """Just open browser for login."""
    browser = await init_browser(headless=False)
    tab = browser.main_tab
    await tab.get("https://www.tiktok.com/login")
    print("\n" + "=" * 60)
    print("  Login ke TikTok manually di browser.")
    print("  Session tersimpan di ~/.tiktok-linkedin/chrome-profile/")
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

    parser = argparse.ArgumentParser(description="TikTok → LinkedIn pipeline")
    parser.add_argument("url", nargs="*", help="TikTok video URL(s)")
    parser.add_argument("--connect", action="store_true", help="Auto-send connection requests")
    parser.add_argument("--no-note", action="store_true", help="Connect without note")
    parser.add_argument("--max", type=int, default=300, help="Max comments")
    parser.add_argument("--scrolls", type=int, default=60, help="Max scrolls")
    parser.add_argument("--login", action="store_true", help="Login only")
    parser.add_argument("--key", help="DeepSeek API key")
    args = parser.parse_args()

    key = args.key or os.environ.get("NINEROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")

    if args.login or not args.url:
        asyncio.run(login_only())
    else:
        for u in args.url:
            print(f"\n\n########## VIDEO: {u} ##########\n")
            asyncio.run(run_pipeline(
                video_url=u,
                deepseek_key=key,
                auto_connect=args.connect,
                max_scrolls=args.scrolls,
                max_comments=args.max,
                no_note=args.no_note,
            ))


if __name__ == "__main__":
    main()
