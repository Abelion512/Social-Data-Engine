#!/usr/bin/env python3
"""Vision collector — saran user: browser + vision, klik visual tanpa selector.

Alur: screenshot panel komentar → kirim ke Gemini vision (9Router) →
LLM beri koordinat expander/scroll target → klik → ulangi. Stop saat N iter
tanpa komentar baru (efisien: hanya dipakai saat jalur selector stuck).

Boros token & lambat — DESIGN: hanya fallback utk reply tersisa (target 55),
bukan collector utama.
"""
import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

import requests
from camoufox.async_api import AsyncCamoufox

sys.path.insert(0, str(Path(__file__).parent))
from src.tiktok_schema import raw_from_dom, write_jsonl

COOKIE_FILE = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"
RAW_DIR = Path(__file__).parent / "data" / "raw"
PROJECT_ENV = Path(__file__).parent / ".env"

URL = "https://www.tiktok.com/@enxayeti/video/7669640839861112071"
VIDEO_ID = "7669640839861112071"

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
    return JSON.stringify(out);
}"""
LEVELS_JS = "document.querySelectorAll('[data-e2e^=\"comment-level-\"]').length"


def _env():
    e = {}
    if PROJECT_ENV.exists():
        for line in PROJECT_ENV.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                e[k.strip()] = v.strip()
    return e


def vision_ask(screenshot_b64: str, prompt: str) -> str:
    """Kirim screenshot + prompt ke Gemini vision via 9Router."""
    env = _env()
    api = (env.get("BASE_URL", "http://localhost:20128/v1").rstrip("/") + "/chat/completions")
    key = env.get("API_KEY", "")
    model = env.get("MODEL_VISION_DEFAULT", "gc/gemini-3.1-flash-lite")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = requests.post(api, json={
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ]}],
            "max_tokens": 300,
            "temperature": 0.0,
            "stream": False,
        }, headers=headers, timeout=60)
        j = r.json()
        return j.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        print(f"[vision] err: {str(e)[:80]}")
        return ""


async def main():
    env = _env()
    if not env.get("API_KEY"):
        print("[vision] .env API_KEY tidak ada — butuh 9Router utk vision")
        return
    cookies = json.loads(COOKIE_FILE.read_text()) if COOKIE_FILE.exists() else []
    today = time.strftime("%Y-%m-%d")
    out_path = RAW_DIR / today / f"{VIDEO_ID}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    all_raw = []
    if out_path.exists():
        for line in out_path.open():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            seen.add(r.get("comment_id", ""))
            all_raw.append(r)
    print(f"[vision] resume {len(all_raw)}")

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
        for i in range(10):
            await page.evaluate(OPEN_PANEL_JS)
            await asyncio.sleep(3)
            if await page.evaluate(LEVELS_JS) > 0:
                print(f"[vision] panel try{i+1}")
                break

        vctx = {"video_id": VIDEO_ID, "video_url": URL, "caption": "", "hashtags": [],
                "creator": "", "create_time": 0, "transcription": ""}

        stale = 0
        for k in range(25):
            # scrape dulu (tambah komentar tanpa vision kalau bisa — hemat token)
            dom = await page.evaluate(DOM_SCRAPE_JS)
            rows = json.loads(dom) if isinstance(dom, str) else []
            added = 0
            for row in rows:
                cid = row.get("comment_id", "")
                if cid in seen:
                    continue
                seen.add(cid)
                all_raw.append(raw_from_dom(row, vctx).to_dict())
                added += 1
            if added:
                print(f"[vision] iter{k+1}: +{added} total={len(all_raw)}")
                write_jsonl(str(out_path), all_raw, append=True)
                stale = 0
            else:
                stale += 1
                # vision call — LLM lihat screenshot, beri koordinat target
                shot = await page.screenshot(full_page=False)
                b64 = base64.b64encode(shot).decode()
                prompt = (
                    "Ini panel komentar TikTok. Ada tombol 'View replies (N)' atau scroll tersembunyi.\n"
                    "Lihat screenshot dan jawab: apa yang harus diklik/scroll untuk memuat komentar berikutnya?\n"
                    "Format JSON: {\"action\": \"click\"|\"scroll\", \"x\": <int>, \"y\": <int>, \"desc\": \"<singkat>\"}\n"
                    "Kalau tidak ada yang bisa diklik lagi, jawab {\"action\": \"done\"}."
                )
                answer = vision_ask(b64, prompt)
                print(f"[vision] iter{k+1} (stale): LLM → {answer[:100]}")
                try:
                    decision = json.loads(answer)
                except Exception:
                    decision = {}
                if decision.get("action") == "done" or decision.get("action") not in ("click", "scroll"):
                    if stale >= 3:
                        print("[vision] done, break")
                        break
                    await asyncio.sleep(2)
                    continue
                x, y = int(decision.get("x", 0)), int(decision.get("y", 0))
                if decision.get("action") == "click" and x and y:
                    await page.mouse.move(x, y)
                    await page.mouse.click(x, y)
                else:
                    await page.mouse.move(400, 400)
                    await page.mouse.wheel(0, 500)
                await asyncio.sleep(4)
            if stale >= 5:
                print(f"[vision] stale {stale}, break")
                break

    print(f"[vision] DONE {len(all_raw)}")


asyncio.run(main())