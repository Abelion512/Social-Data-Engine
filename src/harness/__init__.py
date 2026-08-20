#!/usr/bin/env python3
"""
Pluggable multi-provider harness (deepseek-harness style).

Provider discovery = URL-dispatch to adapter. All providers share canonical
`Observation` so platforms communicate through one schema (TikTok replies,
LinkedIn posts, YouTube, Reddit ...) instead of bespoke pipes.

Usage
  import asyncio
  from src.harness import harness
  obs = asyncio.run(harness.collect("https://www.tiktok.com/@x/video/123"))
  print(harness.resolve("https://www.linkedin.com/posts/...."))  # "linkedin"
  print(harness.probe(url))
"""
from __future__ import annotations

__all__ = ["harness", "Harness", "ProviderAdapter", "register", "resolve", "collect"]

from src.harness.registry import Harness, harness
from src.providers.base import ProviderAdapter

# Re-export for callers
register = harness.register
resolve = harness.resolve
probe = harness.probe


def collect(url: str, **kwargs):
    """Resolve URL → provider → collect (async)."""
    return harness.collect(url, **kwargs)
