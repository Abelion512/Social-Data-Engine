#!/usr/bin/env python3
"""
Pluggable browser-agent harness (manus.im extension style).

NOT API-batch. Each URL routes to a provider whose `toolkit()` offers pilihable
`browser_read`/`browser_click`/`scroll`/`expand_replies`/`route.capture`/`api_fetch`
tools. `BrowserAgent` runs goal-driven observe→plan→act + emits a **transparent
trace** (evidence sampai tuntas).
"""
from __future__ import annotations

__all__ = [
    "harness", "Harness", "BrowserAgent", "AgentTool", "ProviderAdapter",
    "register", "resolve", "probe", "agent", "tools", "collect",
]

from src.harness.registry import Harness, harness
from src.providers.base import ProviderAdapter
from src.harness.agent import BrowserAgent
from src.harness.tools import AgentTool

# module-level aliases backed by the global harness instance
register = harness.register
resolve = harness.resolve
probe = harness.probe
agent = harness.agent
collect = harness.collect


def tools(url: str):
    """Provider-specific tool catalog for `url` (introspection)."""
    return harness.tools(url)
