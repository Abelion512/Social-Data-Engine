#!/usr/bin/env python3
"""
BrowserAgent — goal-driven, observable browser agent (manus.im extension style).

Bukan API-batch. Agent: resolve(provider) → observe page → plan → ACT by
pilih tool → ulang sampai goal terpenuhi. Setiap aksi + observasinya
direkam ke trace log → **transparansi sampai tuntas** (bukti bukan sekadar hasil).

Goals (string, extensible):
  - "collect_all_comments"        scroll + read + api_fetch + route
  - "collect_threaded_replies"   + expand_replies per top-level comment
  - "collect_with_media"         + image_enrich pass

Platform-pluggable: each ProviderAdapter exposes `toolkit()` (override select
JS per platform). Core loop tidak tahu platform — cuma pemilih tool.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.harness.tools import AgentTool, default_toolkit
from src.harness.registry import Harness


@dataclass
class TraceStep:
    """Satu langkah observe→act dalam trace log."""
    goal: str
    tool: str
    observation: str
    result: Dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0


class BrowserAgent:
    """Goal-driven browser agent — operates on a live `page` (camoufox)."""

    GOAL_PLANS = {
        "collect_all_comments": ["read", "scroll", "api_fetch", "route.capture"],
        "collect_threaded_replies": ["read", "expand_replies", "scroll", "api_fetch", "route.capture"],
        "collect_with_media": ["read", "scroll", "image_enrich", "api_fetch"],
    }

    def __init__(self, url: str, goal: str = "collect_all_comments",
                 harness: Harness = None, headless: bool = False):
        self.url = url
        self.goal = goal
        self.h = harness or _default_harness()
        self.session = None          # BrowserSession
        self.page = None
        self.plan: List[str] = list(self.GOAL_PLANS.get(goal, self.GOAL_PLANS["collect_all_comments"]))
        self.trace: List[TraceStep] = []
        self.headless = headless

    # ── provider resolution ────────────────────────────────────────────────
    def provider_name(self) -> str:
        return self.h.resolve(self.url)

    def toolkit(self) -> Dict[str, AgentTool]:
        """Provider-specific toolkit (override select/JS)."""
        provider = self.h.get(self.provider_name())
        kt = getattr(provider, "toolkit", None)
        return kt() if callable(kt) else default_toolkit()

    # ── agent loop: observe → plan → act ──────────────────────────────────
    async def observe(self) -> str:
        """Snapshot current state (counts, scroll) — human-readable."""
        if self.page is None:
            return "no page (browser not connected)"
        try:
            cnt = await self.page.evaluate(
                r"(document.querySelectorAll('[data-e7e][role], [data-e2e*=\"comment\"]').length)"
            )
            return f"page open; ~{cnt} comment nodes visible"
        except Exception as e:
            return f"observe error: {e}"

    def plan_steps(self, observation: str) -> List[str]:
        """Rule-based planner: refine tool order based on what observe saw.
        Simple heuristic — full LLM planner pluggable (see improve.py)."""
        if "no page" in observation:
            return ["connect"]
        return self.plan

    async def act(self, tool_name: str, **arg) -> Dict[str, Any]:
        tools = self.toolkit()
        # route.capture needs fresh captured_pages + counters container.
        if tool_name == "route.capture":
            tool = tools[tool_name]
            assert hasattr(tool, "captured_pages"), "route.capture needs captured_pages+counters"
        tool = tools[tool_name]
        res = await tool.act(self.page, **arg)
        obs = tool.observe(res)
        self.trace.append(TraceStep(goal=self.goal, tool=tool_name,
                                   observation=obs, result=res, ts=_now()))
        return res

    async def run(self) -> Dict[str, Any]:
        """Main agent loop — execute plan, emit full transparent trace."""
        self._log(f"▶ goal='{self.goal}' provider='{self.provider_name()}' url={self.url}")
        # ensure page
        if self.page is None:
            self._log("  → no live page — building trace in DRY-MODE (no browser).")
            for t in ["read"]:
                self._log(f"  ⧖ {t}: skipped (no browser)")
            return self._summary(collected=0)

        observation = await self.observe()
        self._log(f"  ⊘ observe: {observation}")
        steps = self.plan_steps(observation)
        collected = 0
        for tool_name in steps:
            if tool_name == "connect":
                await self._connect(); continue
            try:
                res = await self.act(tool_name)
            except Exception as e:
                self._log(f"  ✗ {tool_name}: ERROR {e}")
                continue
            self._log(f"  ✓ {self.trace[-1].observation}")
            collected += len(res.get("rows", []))
        self._log(f"  ⟘ collected {collected} rows via {len(self.trace)} steps")
        return self._summary(collected=collected)

    # ── page lifecycle ───────────────────────────────────────────────────
    async def _connect(self):
        try:
            from src.browser_selector import BrowserSession
            self.session = BrowserSession()
            ok = await self.session.connect(force_camoufox=self.headless is False)
            self.page = self.session.page
            self._log(f"  ✓ connected: camoufox={self.session.is_camoufox}")
        except Exception as e:
            self._log(f"  ✗ connect failed: {e}")

    # ── output ───────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        self.trace.append(TraceStep(goal=self.goal, tool="__log__",
                                     observation=msg))
        print(msg)

    def _summary(self, collected: int) -> Dict[str, Any]:
        return {
            "url": self.url,
            "provider": self.provider_name(),
            "goal": self.goal,
            "collected": collected,
            "steps": [t.tool for t in self.trace if t.tool != "__log__"],
            "trace": [
                {"tool": t.tool, "observation": t.observation,
                 "result_summary": {k: v for k, v in (t.result or {}).items() if k != "rows"}}
                for t in self.trace
            ],
        }

    def save_trace(self, path: str) -> None:
        json.dump(self._summary(0), open(path, "w"), indent=2, default=str)


def _now():
    import time
    return time.time()


def _default_harness():
    from src.harness.registry import harness
    return harness
