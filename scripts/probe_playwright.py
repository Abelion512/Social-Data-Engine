#!/usr/bin/env python3
"""Probe Playwright: buka panel komentar TikTok dengan robust + interaksi manusiawi."""
import asyncio
import random
from playwright.async_api import async_playwright

URL = "https://www.tiktok.com/@enxayeti/video/7669640839861112071"

OPEN_PANEL_JS = """() => {
    const q = (s) => document.querySelector(s);
    const tryClick = (el) => { if (el) { (el.closest('button') || el).click(); return true; } return false; };
    if (tryClick(q('[data-e2e="comment-icon"]'))) return 'icon';
    if (tryClick(q('[data-e2e="comment-count"]'))) return 'count';
    const btns = document.querySelectorAll('button[aria-label]');
    for (const b of btns) {
        const l = (b.getAttribute('aria-label') || '').toLowerCase();
        if (l.includes('comment') || l.includes('comentar') || l.includes('komentar')) { b.click(); return 'aria:' + l.slice(0, 20); }
    }
    const els = document.querySelectorAll('div[role="button"], span, p, button');
    for (const e of els) {
        const t = (e.textContent || '').trim();
        if (/^View (all )?\\d+ comments?$/i.test(t)) { e.click(); return 'view-all'; }
    }
    return 'none';
}"""

LEVELS_JS = "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length"


async def probe():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/home/abelion/.tiktok-linkedin/chrome-profile",
            headless=False, no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(12)  # kasih waktu SPA settle penuh

        # buka panel dengan retry gigih
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
            await browser.close()
            return

        await asyncio.sleep(5)
        # interaksi manusiawi: scroll halus, gerakan mouse, delay acak
        box = await page.evaluate("""() => {
            const c = document.querySelector('[class*="DivCommentMain"]') ||
                      document.querySelector('[class*="DivCommentListContainer"]');
            if (!c) return null;
            const r = c.getBoundingClientRect();
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }""")
        print("BOX:", box)
        total_before = await page.evaluate(LEVELS_JS)
        print(f"levels awal: {total_before}")

        if box:
            await page.mouse.move(box["x"], box["y"])
            for k in range(15):
                await page.mouse.move(
                    box["x"] + random.randint(-50, 50),
                    box["y"] + random.randint(-30, 30))
                await page.mouse.wheel(0, random.randint(400, 1800))
                await asyncio.sleep(1.2 + random.random() * 1.5)
                if k % 2 == 0:
                    levels = await page.evaluate(LEVELS_JS)
                    print(f"interact{k+1}: levels={levels}")

        await asyncio.sleep(3)
        total_after = await page.evaluate(LEVELS_JS)
        print(f"FINAL: {total_before} → {total_after}")
        await browser.close()


asyncio.run(probe())