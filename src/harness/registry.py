#!/usr/bin/env python3
"""
Provider registry + URL-dispatch harness (pluggable, deepseek-harness style).

Providers self-register at import-time. A URL is routed to the adapter whose
pattern matches first (patterns tried in reverse-registration order →
most-specific last-wins). Each adapter returns canonical `Observation`s, so
all platforms "communicate" through one shared schema instead of N ad-hoc pipes.

Add a platform:
  1. subclass `src.providers.base.ProviderAdapter`
  2. `from src.harness import harness, register`
     `register("myplatform", r"myplatform\\.com/posts/(\\d+)", MyAdapter())`
  3. import your module in `providers/__init__.py`
"""
from __future__ import annotations

import asyncio
import importlib
import re
from typing import Dict, List, Tuple

from src.providers.base import ProviderAdapter


class Harness:
    """Mutable global registry of pluggable data providers."""

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderAdapter] = {}
        # (compiled_regex, provider_name); first match wins
        self._patterns: List[Tuple["re.Pattern[str]", str]] = []

    # ── registration ──────────────────────────────────────────────────────
    def register(self, name: str, pattern: str, adapter: ProviderAdapter) -> ProviderAdapter:
        """Register a provider + its URL pattern."""
        if not isinstance(adapter, ProviderAdapter):
            raise TypeError(f"adapter must subclass ProviderAdapter, got {type(adapter)}")
        self._providers[name] = adapter
        # prepend → latest registration has priority (explicit override)
        self._patterns.insert(0, (re.compile(pattern), name))
        return adapter

    # ── discovery / dispatch ─────────────────────────────────────────────
    def providers(self) -> Dict[str, ProviderAdapter]:
        return dict(self._providers)

    def resolve(self, url: str) -> str:
        """Return provider name whose pattern matches `url` (else ValueError)."""
        for rx, name in self._patterns:
            if rx.search(url):
                return name
        raise ValueError(f"no registered provider for url: {url}")

    def get(self, name: str) -> ProviderAdapter:
        if name not in self._providers:
            raise KeyError(f"provider '{name}' not registered")
        return self._providers[name]

    def probe(self, url: str) -> Dict:
        name = self.resolve(url)
        return self.get(name).probe(url)

    def collect(self, url: str, **kwargs):
        """Async-dispatch collection to the right provider (returns a coroutine)."""
        name = self.resolve(url)
        return self.get(name).collect(url, **kwargs)


# ── global singleton ────────────────────────────────────────────────────────
harness = Harness()


def _import_builtin_providers() -> None:
    """Lazim import built-in adapters so registration side-effect runs."""
    for mod in ("src.providers.tiktok", "src.providers.linkedin"):
        try:
            importlib.import_module(mod)
        except Exception as _e:  # pragma: no cover — optional providers must not break import
            import warnings
            warnings.warn(f"harness: skip provider import {mod}: {_e}")


_import_builtin_providers()


# ── async convenience ─────────────────────────────────────────────────────
def collect_url(url: str, **kwargs):
    """Sync wrapper: `obs = collect_url(url)`."""
    return asyncio.run(harness.collect(url, **kwargs))
