#!/usr/bin/env python3
"""
Human-Act layer — pemenuhan PR #2 point #7/#8.

Filosofi: *human_act first*. Bot yang terlihat seperti bot diblok oleh TikTok
(verify/captcha, rate-limit, slider). Bot yang bertindak seorang manusia — jeda
acak, gerak mouse jittered, scroll natural, fingerprint spoofing, dan **bisa
memecahkan captcha** (image via vision-model, slider via drag) — dapat akses
unlimited via UI (point #8: API ter-limit, UI-based human-action unlimited).

Modul ini pure-stdlib + camoufox/playwright APIs (tidak menambah dependency).
Vision captcha solving memakai 9Router Gemini vision (src/config.py) — **hanya
dipanggil bila captcha sebenarnya muncul** (payout-on-use, tidak tiap komentar).

Komponen:
  - human_delay / human_move_mouse / human_scroll   -> gerak manusiawi
  - apply_stealth(page)                            -> fingerprint spoofing (defense-in-depth di atas camoufox)
  - vision_ask(b64, prompt)                        -> Gemini vision via 9Router
  - detect_captcha(page)                           -> identifikasi slider | image | none
  - solve_slider_captcha / solve_image_captcha     -> penyelesaian UI-driven
  - resolve_captcha_if_present(page)               -> orchestrator (sync-safe)
  - CaptchaSolver(AgentTool)                       -> tool dapat dipilih agent
"""
from __future__ import annotations

import base64
import asyncio
import json
import math
import random
import re
import time
from typing import Any, Dict, Optional

from src.harness.tools import AgentTool

# 9Router vision config (reused, tidak diduplikat)
try:
    from src.config import LLM_API, LLM_KEY, VISION_MODEL
except Exception:  # pragma: no cover — config selalu ada di runtime
    LLM_API, LLM_KEY, VISION_MODEL = "", "", "gc/gemini-3.1-flash-lite"


# ── Human timing (human_act) ─────────────────────────────────────────────────
def human_delay(min_s: float = 0.5, max_s: float = 2.4) -> float:
    """Jeda acak distribusi log-normal — mirip manusia (kebanyakan pendek,
    sesekali lama baca scroll/melongo)."""
    mu = math.log(max(min_s, 0.1))
    sigma = 0.6
    d = random.lognormvariate(mu, sigma)
    d = max(min_s, min(max_s, d))
    time.sleep(d)
    return d


async def ahuman_delay(min_s: float = 0.5, max_s: float = 2.4) -> float:
    mu = math.log(max(min_s, 0.1))
    d = max(min_s, min(max_s, random.lognormvariate(mu, 0.6)))
    await asyncio.sleep(d)
    return d


async def human_move_mouse(page, x: float, y: float, jitter: float = 8.0,
                           steps: int = 12) -> None:
    """Gerakkan mouse ke (x,y) dengan trajekori jittered — bukan garis lurus."""
    cur = await page.evaluate("() => ({ x: window.scrollX||0, y: window.scrollY||0 })") or {"x": 0, "y": 0}
    cur_x, cur_y = float(cur.get("x", 0)), float(cur.get("y", 0))
    for i in range(1, steps + 1):
        f = i / steps
        tx = cur_x + (x - cur_x) * f + random.uniform(-jitter, jitter)
        ty = cur_y + (y - cur_y) * f + random.uniform(-jitter, jitter)
        try:
            await page.mouse.move(tx, ty)
        except Exception:
            pass
        await ahuman_delay(0.0, 0.06)


async def human_scroll(page, delta: Optional[int] = None,
                       times: int = 1) -> None:
    """Scroll natural: besar & arah acak, jeda antar-scroll."""
    if delta is None:
        delta = random.randint(600, 1700)
    for _ in range(times):
        await page.evaluate(f"""(() => {{
            var sc = document.querySelector('div[class*="comments"]') ||
                     document.querySelector('div[data-e2e="comment"]');
            if (sc) sc.scrollBy({{top:{delta}, behavior:'smooth'}});
            else window.scrollBy({{top:{delta}, behavior:'smooth'}});
        }})()""")
        await ahuman_delay(0.9, 2.6)


# ── Stealth fingerprint spoofing (defense-in-depth atop camoufox) ───────────────
STEALTH_JS = """(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['id-ID','id','en-US','en'] });
    Object.defineProperty(navigator, 'language', { get: () => 'id-ID' });
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3].map(function(i){return {filename:'plugin'+i+'.dll', description:'plugin'}}) });
    Object.defineProperty(navigator, 'mimeTypes', { get: () => [1,2].map(function(i){return {type:'type'+i, suffixes:'txt'}}) });
    if (!window.chrome) window.chrome = { runtime: {}, loadTimes:function(){}, csi:function(){return 0;} };
    if (window.navigator.permissions && window.navigator.permissions.query) {
      var q = window.navigator.permissions.query;
      window.navigator.permissions.query = function(p){ return Promise.resolve({state:'granted', query:q}); };
    }
    try { Object.defineProperty(navigator, 'userActivation', { get: () => ({hasUserGesture:true,isActive:true,hasUserGestures:true}) }); } catch(e){}
    Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
    Object.defineProperty(screen, 'availHeight', { get: () => 1032 });
  } catch(e){}
  return 'stealth applied';
})()"""


async def apply_stealth(page) -> str:
    """Inject fingerprint overrides. Dipanggil setelah new_page, sebelum nav."""
    try:
        try:
            return await page.add_init_script(STEALTH_JS) or "stealth registered"
        except Exception:
            return await page.evaluate(STEALTH_JS)
    except Exception as e:
        return f"stealth skip: {e}"


# ── Vision (9Router Gemini) — untuk image captcha ──────────────────────────────
def vision_ask(screenshot_b64: str, prompt: str) -> str:
    """Kirim screenshot (PNG base64) + prompt ke Gemini vision via 9Router.
    Mengembalikan teks jawaban (atau '' bila gagal/kunci tidak ada)."""
    try:
        import requests
    except Exception:
        return ""
    if not LLM_KEY:
        print("[vision] LLM_KEY hilang (9Router) — captcha image tidak dapat diselesaikan otomatis")
        return ""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"}
    try:
        import requests as _req
        r = _req.post(LLM_API, json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ]}],
            "max_tokens": 256,
            "temperature": 0.0,
            "stream": False,
        }, headers=headers, timeout=60)
        j = r.json()
        return (j.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        print(f"[vision] err: {str(e)[:120]}")
        return ""


async def vision_ask_async(screenshot_b64: str, prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, vision_ask, screenshot_b64, prompt)


# ── Captcha detection + resolution ─────────────────────────────────────────────
_DETECT_JS = r"""() => {
    const url = (window.location.href || '').toLowerCase();
    if (/login|verify|captcha/.test(url)) return { present: true, type: 'unknown', reason: 'url:'+url.slice(0,80) };
    const bt = (document.body ? document.body.innerText : '') || '';
    if (/verify|captcha/i.test(bt)) {
      const slider = document.querySelector('[class*="slider"], [class*="Slider"], [class*="drag"], [class*="captcha_drag"], button[class*="captcha"], [id*="slider"]');
      if (slider) return { present: true, type: 'slider', reason: 'slider element' };
      const img = document.querySelector('img[class*="captcha"], canvas[class*="captcha"], [class*="captchaImg"], [class*="puzzle"]');
      if (img) return { present: true, type: 'image', reason: 'captcha image' };
      return { present: true, type: 'unknown', reason: 'verify text' };
    }
    return { present: false, type: 'none', reason: 'ok' };
}"""


async def detect_captcha(page) -> Dict[str, Any]:
    """Deteksi captcha di halaman. Returns {present, type, reason}."""
    try:
        return await page.evaluate(_DETECT_JS)
    except Exception as e:
        return {"present": False, "type": "none", "reason": f"eval error: {e}"}


async def _screenshot_region(page, rect: dict) -> str:
    """Screenshot area -> PNG base64 (clip = {x,y,width,height})."""
    png = await page.screenshot(clip=rect) if rect else await page.screenshot()
    return base64.b64encode(png).decode("ascii")


async def solve_image_captcha(page) -> Dict[str, Any]:
    """Image puzzle captcha: screenshot -> vision -> ketik jawaban."""
    img = await page.evaluate("""() => {
        var i = document.querySelector('img[class*="captcha"], canvas[class*="captcha"], [class*="captchaImg"], [class*="puzzle"]');
        if (!i) return null;
        var r = i.getBoundingClientRect();
        return {x:r.x, y:r.y, width:r.width, height:r.height, tag:i.tagName};
    }""")
    if not img or not img.get("width") or not img.get("height"):
        return {"solved": False, "reason": "no captcha image element"}
    b64 = await _screenshot_region(page, img)
    answer = await vision_ask_async(b64, "What text/characters appear in this captcha image? Return ONLY the answer, no extra words.")
    if not answer:
        return {"solved": False, "reason": "vision returned empty (no key/err)"}
    typed = await page.evaluate("""() => {
        var inp = document.querySelector('input[placeholder*="code"], input[placeholder*="captcha"], input[id*="captcha"], input[id*="code"], input[name*="captcha"]');
        if (inp) { inp.value = ''; inp.focus(); return 'found-input'; }
        return 'no-input';
    }""")
    if typed != "found-input":
        return {"solved": False, "reason": "no captcha text input found"}
    await page.keyboard.type(answer)
    await human_delay(0.3, 0.9)
    await page.keyboard.press("Enter")
    return {"solved": True, "answer": answer[:1], "reason": "typed+submitted"}


async def solve_slider_captcha(page) -> Dict[str, Any]:
    """Slider captcha: locate handle -> human-like drag ke kanan."""
    box = await page.evaluate("""() => {
        var sel = '[class*="slider"], [class*="Slider"], [class*="drag"], button[class*="captcha"], [id*="slider"], .captcha_verify_drag_box, [class*="captcha_drag"]';
        var el = document.querySelector(sel);
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return {x: r.x + r.width/2, y: r.y + r.height/2, width: r.width, height: r.height};
    }""")
    if not box:
        return {"solved": False, "reason": "no slider handle element"}
    await human_move_mouse(page, box["x"], box["y"], jitter=6, steps=10)
    await human_delay(0.4, 1.0)
    target_x = box["x"] + random.randint(220, 320)
    steps = random.randint(12, 18)
    await page.mouse.down()
    for i in range(1, steps + 1):
        f = i / steps
        nx = box["x"] + (target_x - box["x"]) * f + random.uniform(-5, 5)
        await page.mouse.move(nx, box["y"] + random.uniform(-4, 4))
        await ahuman_delay(0.01, 0.05)
    await page.mouse.up()
    await ahuman_delay(1.0, 2.2)
    again = await detect_captcha(page)
    return {"solved": not again.get("present", False),
            "reason": "slider dragged" if not again.get("present") else "still present after drag"}


async def resolve_captcha_if_present(page, attempts: int = 2) -> Dict[str, Any]:
    """Orchestrator: detect -> solve (image or slider). Retry. JANGAN pernah raise."""
    ctype = "none"
    for _ in range(attempts):
        d = await detect_captcha(page)
        if not d.get("present"):
            return {"present": False, "type": "none", "solved": False}
        ctype = d.get("type")
        print(f"[human] captcha detected: {ctype} ({d.get('reason')}) — attempting resolve")
        if ctype == "image":
            res = await solve_image_captcha(page)
        elif ctype == "slider":
            res = await solve_slider_captcha(page)
        else:
            res = {"solved": False, "reason": "unknown captcha type"}
        await ahuman_delay(1.6, 2.8)
        if res.get("solved"):
            return {"present": True, "type": ctype, "solved": True}
    return {"present": True, "type": ctype, "solved": False, "reason": "exhausted attempts"}


# ── AgentTool wrapper (bisa dipilih BrowserAgent) ─────────────────────────────
class CaptchaSolver(AgentTool):
    """AgentTool: deteksi + pecahkan captcha (slider/image) bila ada.
    Dipasang di toolkit TikTok; dipilih agent bila observe() melihat 'verify'."""
    name = "human.solve_captcha"
    description = "detect & resolve TikTok slider/image captcha (vision + human drag)"
    category = "human"

    async def act(self, page, **kwargs) -> Dict[str, Any]:
        res = await resolve_captcha_if_present(page, attempts=kwargs.get("attempts", 2))
        return {"summary": f"captcha resolve: {res}", **res}

    def observe(self, r: Dict) -> str:
        t = "solved" if r.get("solved") else ("none" if not r.get("present") else "failed")
        return f"[captcha:{t}] {r.get('type','-')} ({r.get('reason','')})"
