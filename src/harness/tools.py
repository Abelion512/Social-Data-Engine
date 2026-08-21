#!/usr/bin/env python3
"""
Agent Tools — pilihable, browser-based actions (camoufox/playwright-backed).

Konsep: bukan `collect(url)` API-batch, tapi **goal-driven agent** yang
memilih tool berdasarkan apa yang `observe`-nya lihat (manus.im / browser-agent
style). Setiap tool: act(page, **arg) → observation dict + observe() →
human-readable trace (transparansi sampai tuntas).

Tools reusable di SEMUA platform (TikTok/LinkedIn/YouTube/Reddit) via satu
interface; platform spesifik hanya menimpa selector/strategy lewat `toolkit`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional



# ── AgentTool ABC ─────────────────────────────────────────────────────────────
class AgentTool(ABC):
    """Satu aksi browser yang dapat dipilih agent."""
    name: str = "base"
    description: str = "base agent tool"
    category: str = "browser"

    @abstractmethod
    async def act(self, page, **arg) -> Dict[str, Any]:
        """Eksekusi tool pada page; return structured observation."""

    def observe(self, result: Dict[str, Any]) -> str:
        """Human-readable summary — ditulis ke agent trace log (transparency)."""
        return f"{self.name}: {result.get('summary','')}"


# ── Concrete camoufox/playwright-backed tools ────────────────────────────────
# JS snippets — diimport dari collector.js biar tidak diduplikat. Fall back ke
# inline definisi bila collector belum di-import (circular-safe).
try:
    from src.collector import DOM_SCRAPE_JS, IMAGE_ENRICH_JS, _setup_route_intercept
except Exception:  # pragma: no cover
    DOM_SCRAPE_JS = ""
    IMAGE_ENRICH_JS = ""

try:
    from src.collector import fetch_comments_api as _api_fetch
except Exception:
    _api_fetch = None

try:
    from src.collector import _on_route  # hook, kalau dibutuhkan
except Exception:
    _on_route = None


CLICK_STRATEGIES = {
    "load_more": r"""(() => {
        var el = document.querySelector('[data-e2e*="load-more"], [class*="LoadMore"], [data-e2e*="comment-load"], [data-e2e*="view-more"]');
        if (el) { el.click(); return 'clicked load-more'; }
        return 'no load-more button';
    })()""",
    "expand_replies": r"""(() => {
        var el = document.querySelector('span[class*="reply"], button[class*="reply"], [data-e2e*="comment"], [data-e2e*="reply"]');
        if (el) { el.click(); return 'clicked reply'; }
        var btns = document.querySelectorAll('button');
        for (var i=0;i<btns.length;i++){ if(/reply|balas/i.test(btns[i].ariaLabel||btns[i].textContent||'')){ btns[i].click(); return 'clicked aria-reply'; } }
        return 'no reply button';
    })()""",
    "view_all": r"""(() => {
        document.querySelectorAll('[class*="view-all"], [class*="ViewAll"]').forEach(el => el.click());
        return 'clicked view-all';
    })()""",
}


class BrowserRead(AgentTool):
    """Scrape DOM → comment nodes (camoufox page.evaluate)."""
    name = "browser.read"
    description = "scrape currently-visible comment nodes from DOM"

    async def act(self, page, strategy: str = "comments", **kwargs) -> Dict[str, Any]:
        js = DOM_SCRAPE_JS or "(document.body.innerText)"
        out = await page.evaluate(js)
        # normalisasi hasil scrape menjadi list (format collector)
        rows = out if isinstance(out, list) else []
        return {
            "summary": f"read {len(rows)} comment nodes from DOM",
            "count": len(rows),
            "rows": rows,
        }

    def observe(self, r: Dict) -> str:
        return f"[read] DOM scraped: {r.get('count',0)} comment nodes visible"


class BrowserClick(AgentTool):
    """Human-like click by strategy (load-more / expand_replies / view_all)."""
    name = "browser.click"
    description = "click a UI element (load-more, reply, view-all) — human-like"

    async def act(self, page, strategy: str = "load_more", **kwargs) -> Dict[str, Any]:
        js = CLICK_STRATEGIES.get(strategy)
        if not js:
            return {"summary": f"unknown click strategy: {strategy}", "clicked": False}
        res = await page.evaluate(js)
        return {"summary": res, "clicked": bool(res and "clicked" in str(res))}

    def observe(self, r: Dict) -> str:
        return f"[click:{r.get('summary','')}]"


class ScrollPage(AgentTool):
    """Scroll page (atau comment container) untuk trigger lazy-load / load-more."""
    name = "browser.scroll"
    description = "scroll down to surface more comments"

    async def act(self, page, delta: int = 1600, times: int = 1, **kwargs) -> Dict[str, Any]:
        done = 0
        for _ in range(times):
            # scroll_container: tiktok comment list; fallback window
            await page.evaluate(f"""(() => {{
                var sc = document.querySelector('div[class*=\"comments\"]') ||
                         document.querySelector('div[data-e2e="comment\"]');
                if (sc) sc.scrollBy({{top:{delta}, behavior:'smooth'}});
                else window.scrollBy({{top:{delta}, behavior:'smooth'}});
            }})()""")
            done += 1
        return {"summary": f"scrolled {done}x (delta={delta})"}

    def observe(self, r: Dict) -> str:
        return f"[scroll] {r.get('summary','')}"


class ExpandReplies(AgentTool):
    """Klik 'view more replies' pada thread tertentu (per-comment)."""
    name = "browser.expand_replies"
    description = "expand reply threads for a comment (recursive depth++)"

    async def act(self, page, comment_id: Optional[str] = None, depth: int = 0, **kwargs) -> Dict[str, Any]:
        js = CLICK_STRATEGIES["expand_replies"]
        res = await page.evaluate(js)
        return {"summary": res, "comment_id": comment_id, "depth": depth}

    def observe(self, r: Dict) -> str:
        return f"[expand_replies] {r.get('summary','')} (depth={r.get('depth',0)})"


class RouteCapture(AgentTool):
    """Intercept TikTok comment API via Playwright route() (camoufox)."""
    name = "route.capture"
    description = "register route intercept for comment/list API responses"

    def __init__(self, captured_pages: list, counters: dict):
        super().__init__()
        self.captured_pages = captured_pages
        self.counters = counters

    async def act(self, page, **kwargs) -> Dict[str, Any]:
        await _setup_route_intercept(page, self.captured_pages, self.counters)
        # _setup_route_intercept registers page.route("**/*", _on_route)
        registered = self.counters.get("route", 0) > 0 or True
        return {"summary": "route intercept registered", "registered": registered}

    def observe(self, r: Dict) -> str:
        return f"[route] intercept registered; captured={len(self.captured_pages)}"


class ApiFetch(AgentTool):
    """Fetch comments via TikTok internal API (page.evaluate, same-origin cookie)."""
    name = "api.fetch"
    description = "fetch comment page from TikTok internal API"

    async def act(self, page, video_id: str, cursor: int = 0, **kwargs) -> Dict[str, Any]:
        if _api_fetch is None:
            return {"summary": "api fetch unavailable", "rows": []}
        data = await _api_fetch(page, video_id=video_id, cursor=cursor) or {}
        rows = data.get("comments", []) if isinstance(data, dict) else []
        return {"summary": f"api fetched page cursor={cursor}", "rows": rows,
                "cursor": data.get("cursor") if isinstance(data, dict) else None}

    def observe(self, r: Dict) -> str:
        return f"[api] {r.get('summary','')} fetched {len(r.get('rows',[]))} rows"


class ImageEnrich(AgentTool):
    """Lazy-load + re-scrape media (photo/sticker) on known comment IDs."""
    name = "media.enrich"
    description = "scroll into view + capture img/background-image/video[poster]"

    async def act(self, page, comment_ids: List[str], **kwargs) -> Dict[str, Any]:
        if not IMAGE_ENRICH_JS:
            return {"summary": "image enrich js unavailable", "rows": []}
        out = await page.evaluate(IMAGE_ENRICH_JS, comment_ids)
        return {"summary": "media enrichment pass", "rows": out or []}

    def observe(self, r: Dict) -> str:
        return f"[media] enriched {len(r.get('rows',[]))} media-bearing comments"


def default_toolkit() -> Dict[str, AgentTool]:
    """Default toolset — platform can override selectors/strategies."""
    return {
        "read": BrowserRead(),
        "click": BrowserClick(),
        "scroll": ScrollPage(),
        "expand_replies": ExpandReplies(),
        "api_fetch": ApiFetch(),
        "image_enrich": ImageEnrich(),
    }
