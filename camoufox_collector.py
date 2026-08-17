#!/usr/bin/env python3
"""Camoufox collector — jalur pasti menuju 55 komentar (cookie + scroll presisi).

Nichttrace: jalur nodriver stuck di 20 (scroll di-block). Camoufox + cookie
Chrome + wheel presisi + reopen panel terbukti: 36 di probe. Fungsi ini
menulis raw langsung, target full.
"""
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

sys.path.insert(0, str(Path(__file__).parent))
from src.tiktok_schema import RawComment, Author, raw_from_dom, write_jsonl, COLLECTOR_VERSION

PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
COOKIE_FILE = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"
STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"
DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"

OPEN_PANEL_JS = """() => {
    const q = (s) => document.querySelector(s);
    const tc = (el) => { if (el) { (el.closest('button') || el).click(); return true; } return false; };
    if (tc(q('[data-e2e="comment-icon"]'))) return 'icon';
    if (tc(q('[data-e2e="comment-count"]'))) return 'count';
    const els = document.querySelectorAll('div[role="button"], span, p, button');
    for (const e of els) {
        const t = (e.textContent || '').trim();
        if (/^View (all )?\\d+ comments?$/i.test(t)) { e.click(); return 'view-all'; }
    }
    return 'none';
}"""

DOM_SCRAPE_JS = """() => {
    const out = [];
    const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
    items.forEach(el => {
        const raw = el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
        if ((!raw || raw.length < 3)) return;
        const wrapper = el.closest('[class*="DivCommentObjectWrapper"], [data-e2e^="comment-item-"]') || el.parentElement;
        let uname = '';
        if (wrapper) {
            const a = wrapper.querySelector('a[href*="/@"]');
            uname = a ? a.getAttribute('href').replace(/^\\/?@/, '').split('?')[0] : '';
        }
        let cid = el.getAttribute('data-e2e') || '';
        if (!cid || /^comment-level-\\d+$/.test(cid)) {
            let h = 0;
            const s = (uname + '|' + raw).substring(0, 120);
            for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff;
            cid = 'dom_' + h.toString(36);
        }
        const imgs = Array.from(el.querySelectorAll('img[src*="tiktokcdn"], img[src^="http"]'))
            .map(i => i.src).filter(s => s && !s.includes('static/') && !s.includes('avatar')).slice(0, 4);
        out.push({ raw: raw.substring(0, 400), username: uname || raw.split(' ')[0], images: imgs, comment_id: cid });
    });
    const seen = new Set();
    return JSON.stringify(out.filter(o => (seen.has(o.raw + '|' + o.images.join(',')) ? false : (seen.add(o.raw + '|' + o.images.join(',')), true))));
}"""

LEVELS_JS = "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length"


def _load_cookies():
    if COOKIE_FILE.exists():
        return json.loads(COOKIE_FILE.read_text())
    return []


async def collect_camoufox(video_url: str, max_scrolls: int = 80, max_comments: int = 300):
    m = re.search(r"/(?:video|photo)/(\d+)", video_url)
    if not m:
        print(f"[camoufox] invalid URL {video_url}")
        return {"error": "invalid_url"}
    video_id = m.group(1)
    today = time.strftime("%Y-%m-%d")
    out_path = RAW_DIR / today / f"{video_id}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cookies = _load_cookies()
    print(f"[camoufox] cookies: {len(cookies)}")

    all_raw: list = []
    seen: set = set()

    async with AsyncCamoufox(headless=False) as browser:
        page = await browser.new_page()
        try:
            await page.goto("https://www.tiktok.com", wait_until="commit", timeout=45000)
            await asyncio.sleep(8)
            if cookies:
                try:
                    await page.context.add_cookies([
                        {"name": c["name"], "value": c["value"], "domain": ".tiktok.com", "path": "/"}
                        for c in cookies if c.get("name")
                    ])
                    print("[camoufox] cookies injected")
                except Exception as e:
                    print(f"[camoufox] cookie inject err: {str(e)[:80]}")
        except Exception as e:
            print(f"[camoufox] tiktok.com err: {str(e)[:80]}")

        await page.goto(video_url, wait_until="commit", timeout=60000)
        await asyncio.sleep(15)

        # buka panel
        opened = False
        for i in range(12):
            r = await page.evaluate(OPEN_PANEL_JS)
            await asyncio.sleep(3)
            n = await page.evaluate(LEVELS_JS)
            print(f"[camoufox] panel try{i+1}: {r} levels={n}")
            if n > 0:
                opened = True
                break
        if not opened:
            print("[camoufox] PANEL GAGAL")
            return {"error": "panel_failed", "video_id": video_id}

        video_ctx = {"video_id": video_id, "video_url": video_url, "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""}

        # capture + scroll presisi
        for k in range(max_scrolls):
            # scrape DOM
            dom_str = await page.evaluate(DOM_SCRAPE_JS)
            try:
                rows = json.loads(dom_str) if isinstance(dom_str, str) else (dom_str or [])
            except Exception:
                rows = []
            dom_added = 0
            for row in rows:
                cid = row.get("comment_id", "")
                if cid in seen:
                    continue
                seen.add(cid)
                all_raw.append(raw_from_dom(row, video_ctx))
                dom_added += 1

            # tulis incremental
            write_jsonl(str(out_path), [r.to_dict() for r in all_raw], append=True)
            print(f"[camoufox] iter{k+1}: +{dom_added} total={len(all_raw)}")

            n = await page.evaluate(LEVELS_JS)
            if len(all_raw) >= max_comments:
                print(f"[camoufox] cap {max_comments}")
                break

            # EXPAND REPLIES: komentar punya reply tersembunyi (View replies (N))
            # — 55 termasuk reply, bukan top-level saja. Klik semua expander.
            await page.evaluate("""() => {
                const toClick = [];
                document.querySelectorAll('[data-e2e^="view-replies"], [data-e2e*="view-more-"], [class*="ReplyActionText"], [class*="ViewReplies"], button, div[role="button"]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (/^View (all )?repl/i.test(t) || /replies?$/i.test(t) || /^Lihat (semua )?balasan/i.test(t) || t.includes('replies') || t.includes('balasan')) {
                        if (!el.dataset._ttc) { el.dataset._ttc = '1'; toClick.push(el); }
                    }
                });
                // selector reply-count Tiktok: "View all replies"
                document.querySelectorAll('span, div, p').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (/^View all \\d+ replies?$/i.test(t) || /^Lihat (semua )?\\d+ balasan/i.test(t) || /^View \\d+ replies?$/i.test(t)) {
                        if (!el.dataset._ttc) { el.dataset._ttc = '1'; toClick.push(el); }
                    }
                });
                toClick.forEach(el => { try { el.click(); } catch (e) {} });
                return toClick.length;
            }""")

            # scroll presisi: scrollIntoView item terakhir + wheel kecil + reopen bila tutup
            await page.evaluate("""() => {
                const items = document.querySelectorAll('[data-e2e^="comment-level-"]');
                if (items.length) items[items.length - 1].scrollIntoView({ block: 'end' });
                const c = document.querySelector('[class*="DivCommentMain"]');
                if (c) c.scrollTop = c.scrollHeight;
            }""")
            box = await page.evaluate("""() => {
                const c = document.querySelector('[class*="DivCommentMain"]');
                if (!c) return null; const r = c.getBoundingClientRect();
                return { x: r.x + r.width/2, y: Math.min(r.y + r.height - 5, window.innerHeight - 5) };
            }""")
            if box:
                await page.mouse.move(box["x"], box["y"])
                await page.mouse.wheel(0, random.randint(150, 500))
            # scroll-bounce: kadang TikTok load saat scroll balik ke atas
            if k % 6 == 3:
                await page.mouse.wheel(0, -random.randint(800, 1500))
                await asyncio.sleep(1)
                await page.mouse.wheel(0, random.randint(800, 1500))
            await asyncio.sleep(2.5 + random.random() * 2.5)

            # panel tutup? reopen
            if await page.evaluate(LEVELS_JS) == 0 and len(all_raw) > 0:
                await page.evaluate(OPEN_PANEL_JS)
                await asyncio.sleep(3)

            # SIKLUS PANEL: tiap ~12 iter, tutup+buka panel — TikTok sering
            # memuat batch baru per pembukaan panel
            if k > 0 and k % 12 == 0:
                print("[camoufox] cycle: tutup-buka panel")
                await page.keyboard.press("Escape")
                await asyncio.sleep(2)
                await page.evaluate(OPEN_PANEL_JS)
                await asyncio.sleep(5)

            # stop: 14 iter tanpa tambahan (TikTok rate-limit batch, butuh sabar)
            if k >= 14 and all_raw and _last_new == 0:
                print("[camoufox] stale, break")
                break
            _last_new = dom_added

    print(f"[camoufox] DONE {len(all_raw)} → {out_path}")
    return {"video_id": video_id, "output": str(out_path), "comments": len(all_raw)}


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tiktok.com/@enxayeti/video/7669640839861112071"
    asyncio.run(collect_camoufox(url))