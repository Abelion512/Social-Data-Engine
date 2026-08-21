#!/usr/bin/env python3
"""Probe Camoufox: browser anti-fingerprint + interaksi manusiawi → target 55 komentar."""
import asyncio
import random
from camoufox.async_api import AsyncCamoufox

URL = "https://www.tiktok.com/@enxayeti/video/7669640839861112071"

OPEN_PANEL_JS = """() => {
    const q = (s) => document.querySelector(s);
    const tryClick = (el) => { if (el) { (el.closest('button') || el).click(); return true; } return false; };
    if (tryClick(q('[data-e2e="comment-icon"]'))) return 'icon';
    if (tryClick(q('[data-e2e="comment-count"]'))) return 'count';
    const btns = document.querySelectorAll('button[aria-label]');
    for (const b of btns) {
        const l = (b.getAttribute('aria-label') || '').toLowerCase();
        if (l.includes('comment') || l.includes('comentar') || l.includes('komentar')) { b.click(); return 'aria'; }
    }
    const els = document.querySelectorAll('div[role="button"], span, p, button');
    for (const e of els) {
        const t = (e.textContent || '').trim();
        if (/^View (all )?\\d+ comments?$/i.test(t)) { e.click(); return 'view-all'; }
    }
    return 'none';
}"""

LEVELS_JS = "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length"
REPORT_JS = "document.querySelector('[data-e2e=\"comment-count\"]') ? (document.querySelector('[data-e2e=\"comment-count\"]').getAttribute('title')||'') : 'no-count'"


async def main():
    async with AsyncCamoufox(headless=False) as browser:
        page = await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(12)

        opened = None
        for i in range(10):
            result = await page.evaluate(OPEN_PANEL_JS)
            await asyncio.sleep(3)
            levels = await page.evaluate(LEVELS_JS)
            print(f"try{i+1}: {result} levels={levels}")
            if levels > 0:
                opened = result
                break
        if not opened:
            print("PANEL GAGAL TERBUKA")
            return

        reported = await page.evaluate(REPORT_JS)
        print("reported:", reported)
        await asyncio.sleep(4)

        box = await page.evaluate("""() => {
            const c = document.querySelector('[class*="DivCommentMain"]') ||
                      document.querySelector('[class*="DivCommentListContainer"]');
            if (!c) return null;
            const r = c.getBoundingClientRect();
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }""")
        print("BOX:", box)

        if box:
            await page.mouse.move(box["x"], box["y"])
            for k in range(20):
                await page.mouse.move(
                    box["x"] + random.randint(-60, 60),
                    box["y"] + random.randint(-40, 40))
                await page.mouse.wheel(0, random.randint(300, 2000))
                await asyncio.sleep(1.5 + random.random() * 2)
                if k % 3 == 0:
                    levels = await page.evaluate(LEVELS_JS)
                    print(f"interact{k+1}: levels={levels}")

        await asyncio.sleep(3)
        print("FINAL levels:", await page.evaluate(LEVELS_JS))


asyncio.run(main())