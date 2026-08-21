#!/usr/bin/env python3
"""Siklus Camoufox stabil: restart browser per batch (anti-EPIPE) → target 55.

Strategi: Camoufox crash (~2 batch expand). Solusi: batasi batch per browser
(12 iter), close → buka baru + cookie → lanjut dengan seen_ids persist.
"""
import asyncio
import json
import random
import sys
import time
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

sys.path.insert(0, str(Path(__file__).parent))
from src.tiktok_schema import raw_from_dom, write_jsonl

URL = "https://www.tiktok.com/@enxayeti/video/7669640839861112071"
COOKIE_FILE = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"
RAW_DIR = Path(__file__).parent / "data" / "raw"

OPEN_PANEL_JS = """() => {
    const q = (s) => document.querySelector(s);
    const tc = (el) => { if (el) { (el.closest('button') || el).click(); return true; } return false; };
    if (tc(q('[data-e2e="comment-icon"]'))) return true;
    if (tc(q('[data-e2e="comment-count"]'))) return true;
    return false;
}"""
DOM_SCRAPE_JS = """() => {
    const out = [];
    document.querySelectorAll('[data-e2e^="comment-level-"]').forEach(el => {
        const raw = el.textContent ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
        if (!raw || raw.length < 3) return;
        const wrapper = el.closest('[class*="DivCommentObjectWrapper"]') || el.parentElement;
        let uname = '';
        if (wrapper) { const a = wrapper.querySelector('a[href*="/@"]'); uname = a ? a.getAttribute('href').replace(/^\\/?@/, '').split('?')[0] : ''; }
        let cid = el.getAttribute('data-e2e') || '';
        if (!cid || /^comment-level-\\d+$/.test(cid)) {
            let h = 0; const s = (uname + '|' + raw).substring(0, 120);
            for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff;
            cid = 'dom_' + h.toString(36);
        }
        out.push({ raw: raw.substring(0, 400), username: uname, images: [], comment_id: cid });
    });
    const seen = new Set();
    return JSON.stringify(out.filter(o => (seen.has(o.raw + '|' + o.images.join(',')) ? false : (seen.add(o.raw + '|' + o.images.join(',')), true))));
}"""
EXPAND_JS = """() => {
    const toClick = [];
    document.querySelectorAll('span, div, p, button').forEach(el => {
        const t = (el.textContent || '').trim();
        if (/^View (all )?\\d+ replies?$/i.test(t) || /^Lihat (semua )?\\d+ balasan/i.test(t) || /^View replies?$/i.test(t)) {
            if (!el.dataset._ttc) { el.dataset._ttc = '1'; toClick.push(el); }
        }
    });
    toClick.forEach(el => { try { el.click(); } catch (e) {} });
    return toClick.length;
}"""
LEVELS_JS = "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length"


async def one_browser_cycle(page, video_id, video_url, seen, all_raw, out_path, max_iter=12):
    """Satu siklus browser: open panel + expand + scroll (max_iter). Return added."""
    vctx = {"video_id": video_id, "video_url": video_url, "caption": "", "hashtags": [],
            "creator": "", "create_time": 0, "transcription": ""}
    added_total = 0
    for i in range(max_iter):
        try:
            n_exp = await page.evaluate(EXPAND_JS)
            if n_exp:
                await asyncio.sleep(3)
            dom = await page.evaluate(DOM_SCRAPE_JS)
            rows = json.loads(dom) if isinstance(dom, str) else []
            added = 0
            for row in rows:
                cid = row.get("comment_id", "")
                if cid in seen:
                    continue
                seen.add(cid)
                # simpan dict selalu (konsisten dengan resume dari file)
                all_raw.append(raw_from_dom(row, vctx).to_dict())
                added += 1
            if added:
                added_total += added
                print(f"[cycle] loop{i+1}: +{added} total={len(all_raw)}")
            write_jsonl(str(out_path), all_raw, append=True)
            # scroll + expand
            await page.evaluate("""() => { const items = document.querySelectorAll('[data-e2e^="comment-level-"]'); if (items.length) items[items.length-1].scrollIntoView({block:'end'}); const c = document.querySelector('[class*="DivCommentMain"]'); if (c) c.scrollTop = c.scrollHeight; }""")
            box = await page.evaluate("""() => { const c = document.querySelector('[class*="DivCommentMain"]'); if (!c) return null; const r = c.getBoundingClientRect(); return {x: r.x+r.width/2, y: Math.min(r.y+r.height-5, window.innerHeight-5)}; }""")
            if box:
                await page.mouse.move(box["x"], box["y"])
                await page.mouse.wheel(0, random.randint(150, 500))
            await asyncio.sleep(2.5 + random.random() * 2)
        except Exception as e:
            print(f"[cycle] err loop{i+1}: {str(e)[:60]}")
            await asyncio.sleep(2)
            break
    return added_total


async def main():
    video_id = "7669640839861112071"
    cookies = json.loads(COOKIE_FILE.read_text()) if COOKIE_FILE.exists() else []
    today = time.strftime("%Y-%m-%d")
    out_path = RAW_DIR / today / f"{video_id}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    all_raw = []
    # load existing raw (resume)
    if out_path.exists():
        for line in out_path.open():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            seen.add(r.get("comment_id", ""))
            all_raw.append(r)
    print(f"[start] resume: {len(all_raw)} existing")

    for cyc in range(5):  # maks 5 siklus browser
        if len(all_raw) >= 55:
            break
        print(f"\n=== SIKLUS {cyc+1} ===")
        try:
            async with AsyncCamoufox(headless=False) as browser:
                page = await browser.new_page()
                await page.goto("https://www.tiktok.com", wait_until="commit", timeout=45000)
                await asyncio.sleep(6)
                if cookies:
                    await page.context.add_cookies([
                        {"name": c["name"], "value": c["value"], "domain": ".tiktok.com", "path": "/"}
                        for c in cookies if c.get("name")
                    ])
                await page.goto(URL, wait_until="commit", timeout=60000)
                await asyncio.sleep(12)
                # buka panel
                opened = False
                for i in range(10):
                    await page.evaluate(OPEN_PANEL_JS)
                    await asyncio.sleep(3)
                    if await page.evaluate(LEVELS_JS) > 0:
                        opened = True
                        break
                if not opened:
                    print("[cycle] panel gagal (flaky TikTok), siklus lanjut")
                    await asyncio.sleep(3)
                    continue
                got = await one_browser_cycle(page, video_id, URL, seen, all_raw, out_path)
                print(f"[cycle {cyc+1}] got {got}, total {len(all_raw)}")
        except Exception as e:
            print(f"[cycle {cyc+1}] CRASH: {str(e)[:80]}")
            await asyncio.sleep(5)

    print(f"\nFINAL: {len(all_raw)} komentar")
    print(f"reported=55 captured={len(all_raw)} coverage={round(len(all_raw)/55, 3)}")


asyncio.run(main())