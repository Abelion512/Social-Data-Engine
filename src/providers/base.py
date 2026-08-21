#!/usr/bin/env python3
"""
Provider Adapter Interface — Multi-provider data collection abstraction.

Setiap provider (TikTok, LinkedIn, YouTube, Reddit) harus implementasi
adaptor ini. Interface uniform memungkinkan pipeline untuk berurusan
dengan satu antarmuka meski sumber data berbeda-beda.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict

from src.schema.canonical import Observation


class ProviderAdapter(ABC):
    """Antarmuka dasar untuk provider data sosial."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Nama unik provider — gunakan di entity_id dan provenance."""

    @abstractmethod
    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """
        Kumpulkan data dari provider ini.

        Args:
            url: URL sumber (video, post, thread)
            **kwargs: opsi tambahan (max_comments, cursor, dst)

        Returns:
            List[Observation] — hasil kumpulan dalam schema kanonikal
        """

    @abstractmethod
    def probe(self, url: str) -> Dict:
        """
        Cek ketersediaan dan metadata sumber.

        Returns:
            Dict dengan keys: url, provider, accessible, metadata
        """


class AgentProvider(ProviderAdapter):
    """Provider berbasis **browser agent** (bukan API-batch).

    Mengganti semangkuk `collect(url)` API-batch dengan `BrowserAgent` yang
    memilih `browser_*` tools secara goal-driven (manus.im extension style).
    `collect()` tetap ada sebagai backward-compat wrapper → agent.run.
    """

    default_goal: str = "collect_all_comments"

    def toolkit(self) -> Dict[str, "AgentTool"]:  # noqa: F821 (lazy import)
        """Default toolkit — overridable per platform."""
        from src.harness.tools import default_toolkit, RouteCapture
        kt = default_toolkit()
        # route.capture is stateful — fresh container per toolkit instance
        kt["route.capture"] = RouteCapture(captured_pages=[], counters={})
        return kt

    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """Backward-compat: delegate ke agent run (no-op without browser)."""
        from src.harness.agent import BrowserAgent
        a = BrowserAgent(url, goal=kwargs.get("goal", self.default_goal))
        await a.run()
        return []  # observations are in data/{raw,normalized,curation} — see trace

    async def run_agent(self, url: str, goal: str = None, headless: bool = False) -> Dict:
        """Run goal-driven agent on this URL — return full transparent trace."""
        from src.harness.agent import BrowserAgent
        a = BrowserAgent(url, goal=goal or self.default_goal, headless=headless)
        return await a.run()


# Re-export
from src.harness.tools import AgentTool  # noqa: E402