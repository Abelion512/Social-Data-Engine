#!/usr/bin/env python3
"""
Pluggable browser-agent harness (manus.im extension style).

NOT API-batch. Each URL routes to a provider whose `toolkit()` offers pilihable
`browser_read`/`browser_click`/`scroll`/`expand_replies`/`route.capture`/`api_fetch`
tools. `BrowserAgent` runs goal-driven observe→plan→act + emits a **transparent
trace** (evidence sampai tuntas).

Import cost is deliberate: the registry (and with it every provider) is eager,
while the browser layer — `agent` → `tools` → `collector` → `browser_selector`
(playwright/camoufox/urllib.request, ~50 ms) — is resolved on first attribute
access via PEP 562 `__getattr__`. A registry-only caller (MCP tool call, plugin
bridge, `--list-plugins`) therefore never pays for a browser stack it will not
use, and the SDD §3 boundary (runtime/host layers do not depend on providers'
browser plumbing) stays visible in the import graph.
"""
from __future__ import annotations

import importlib

__all__ = [
    "harness", "Harness", "BrowserAgent", "AgentTool", "ProviderAdapter",
    "register", "resolve", "probe", "agent", "tools", "collect",
]

from src.harness.registry import Harness, harness

# module-level aliases backed by the global harness instance (cheap: registry only)
register = harness.register
resolve = harness.resolve
probe = harness.probe
agent = harness.agent
collect = harness.collect


def tools(url: str):
    """Provider-specific tool catalog for `url` (introspection)."""
    return harness.tools(url)


# name → (module, attribute). Resolved on first access, not at import.
_LAZY_ATTRS = {
    "ProviderAdapter": ("src.providers.base", "ProviderAdapter"),
    "BrowserAgent": ("src.harness.agent", "BrowserAgent"),
    "AgentTool": ("src.harness.tools", "AgentTool"),
}
# Submodules that used to be imported by this package; keep `src.harness.agent`
# style attribute access working without importing them eagerly.
_LAZY_SUBMODULES = ("agent", "human", "tools", "registry")


def __getattr__(name: str):
    if name in _LAZY_ATTRS:
        module_name, attr = _LAZY_ATTRS[name]
        return getattr(importlib.import_module(module_name), attr)
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(f"src.harness.{name}")
    raise AttributeError(f"module 'src.harness' has no attribute {name!r}")


def __dir__():
    return sorted(set(__all__) | set(_LAZY_SUBMODULES))
