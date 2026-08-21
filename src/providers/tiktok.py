#!/usr/bin/env python3
"""
TikTok Provider Adapter — Camoufox-based comment collector.

Merancang ulang collector.py menjadi adapter yang konsumenkan
antarmuka ProviderAdapter. Pipeline hanya perlu memanggil collect()
dan menerima Observation list — tidak peduli apakah source-nya
TikTok, LinkedIn, YouTube, atau Reddit.
"""
from __future__ import annotations

from typing import List, Dict

from src.providers.base import AgentProvider
from src.harness.registry import harness
from src.schema.canonical import Observation
from src.schema.mapper import tiktok_to_canonical
from src.tiktok_schema import RawComment

try:
    from camoufox.async_api import AsyncCamoufox
except Exception:
    AsyncCamoufox = None

try:
    from src.browser_selector import BrowserSession
except Exception:
    BrowserSession = None


class TikTokAdapter(AgentProvider):
    """
    Adapter TikTok — goal-driven browser agent (manus.im extension style).

    `collect()` API-batch tetap ada (backward-compat, delegasi ke agent).
    Agent memilih browser.* tools via `toolkit()` — platform spesifik.
    """

    default_goal = "collect_threaded_replies"

    @property
    def provider_name(self) -> str:
        return "tiktok"

    def toolkit(self):
        """TikTok-specific tool bindings + reply-thread strategy."""
        from src.harness.tools import default_toolkit, RouteCapture
        from src.harness.human import CaptchaSolver
        kt = default_toolkit()
        # TikTok: replies under [data-e2e="comment"] threads; route captures
        # /comment/list/reply — parent_comment_id parsed di collector._on_route.
        # Stateful container (fresh per toolkit) -> thread-aware capture.
        kt["route.capture"] = RouteCapture(captured_pages=[], counters={"route": 0, "api": 0, "dom": 0})
        # human_act: captcha resolve tool (slider/image) selectable oleh agent.
        kt["solve_captcha"] = CaptchaSolver()
        return kt

    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """
        Kumpulkan komentar TikTok.

        Args:
            url: URL video TikTok
            max: maksimal komentar (default 100)
            scrolls: jumlah scroll (default 10)
            resume: job_id untuk melanjutkan
            capture_method: 'cdp' | 'dom' | 'auto'

        Returns:
            List[Observation] dalam schema kanonikal
        """
        max_comments = kwargs.get("max", 100)
        scrolls = kwargs.get("scrolls", 10)
        resume_job = kwargs.get("resume")
        capture_method = kwargs.get("capture_method", "auto")

        raw_comments = await self._collect_comments(
            url, max=max_comments, scrolls=scrolls,
            resume=resume_job, capture_method=capture_method
        )

        # Convert ke canonical schema
        observations = [tiktok_to_canonical(r) for r in raw_comments]
        return observations

    def probe(self, url: str) -> Dict:
        """Cek apakah URL TikTok dapat diakses."""
        return {
            "url": url,
            "provider": self.provider_name,
            "accessible": None,
            "metadata": {}
        }

    # ── Internal collector methods ──────────────────────────────────────────────

    async def _collect_comments(self, url: str, **kwargs) -> List[RawComment]:
        """
        Core collector — Camoufox + DOM/CDP.
        Mengembalikan list RawComment untuk dikonversi di layer mapper.
        """
        # Import sini untuk menghindari circular import
        from src.collector import collect_comments
        return await collect_comments(url, **kwargs)


# Register TikTok as a routable provider on import (lazy side-effect).
harness.register("tiktok", r"tiktok\.com", TikTokAdapter())
