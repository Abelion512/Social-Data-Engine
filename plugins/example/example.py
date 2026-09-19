#!/usr/bin/env python3
"""
Example plugin — template for adding a new platform WITHOUT touching core.

Copy this directory (plugins/example/) to plugins/<yourplatform>/, rename the
module file to match, and replace the three marked pieces:

  1. URL_PATTERN   — regex that routes your platform's URLs to this adapter
  2. probe()       — cheap availability/metadata check
  3. collect()     — real acquisition, returning canonical Observations

The example itself is a *deterministic demo provider*: `collect()` synthesizes
Observations from the URL without any browser/network, so it doubles as the
end-to-end fixture for the plugin contract tests (tests/test_plugins.py).

Kontrak (details: src/plugins.py + docs/AGENT-GUIDE.md):
  - subclass ProviderAdapter (atau AgentProvider bila pakai browser agent)
  - register via harness.register(name, pattern, adapter) at import
  - kembalikan List[Observation] canonical — satu schema untuk semua platform
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List

from src.harness.registry import harness
from src.providers.base import ProviderAdapter
from src.schema.canonical import Content, Observation, Provenance

URL_PATTERN = r"example\.social/(?:post|video)/(\d+)"

PLUGIN_VERSION = "0.1.0"
PIPELINE_VERSION = "1.0.0"


class ExampleAdapter(ProviderAdapter):
    """Demo provider: deterministic Observations from the URL (no network)."""

    @property
    def provider_name(self) -> str:
        return "example"

    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """Synthesize N=3 canonical observations keyed by the content id.

        Replace this body with real acquisition (browser agent or API).
        """
        m = re.search(URL_PATTERN, url)
        if not m:
            raise ValueError(f"example plugin: url tidak cocok: {url}")
        content_id = m.group(1)
        n = int(kwargs.get("max_comments", 3) or 3)
        out: List[Observation] = []
        for i in range(min(max(n, 1), 100)):  # bounded — hard cap 100
            oid = f"example:{content_id}:{i}"
            text = f"[demo] comment {i} on {content_id}"
            out.append(Observation(
                observation_id=oid,
                source="example",
                content=Content(text_raw=text, text_normalized=text.lower()),
                provenance=Provenance(
                    collector_version=PLUGIN_VERSION,
                    pipeline_version=PIPELINE_VERSION,
                    source="example",
                ),
            ))
        return out

    def probe(self, url: str) -> Dict:
        m = re.search(URL_PATTERN, url)
        return {
            "url": url,
            "provider": "example",
            "accessible": bool(m),
            "metadata": {
                "content_id": m.group(1) if m else "",
                "deterministic_demo": True,
                "fingerprint": hashlib.sha256(url.encode()).hexdigest()[:12],
            },
        }


# Register on import — this side-effect is the whole plugin contract.
harness.register("example", URL_PATTERN, ExampleAdapter())
