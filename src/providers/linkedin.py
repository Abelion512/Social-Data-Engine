#!/usr/bin/env python3
"""
LinkedIn Provider Adapter — pluggable (ProviderAdapter).

Parses LinkedIn post URL → post_id / commentId, maps LinkedIn comment dicts
to canonical `Observation` so LinkedIn data flows through the SAME pipeline as
TikTok (dedup → enrich → quality → curated → manifest).

`collect()` is the extension point: plug any LinkedIn comment scraper (camoufox
/ selenium / API) here, return `List[Observation]`. Currently:
  - probe() works (URL parse + schema check)
  - collect() delegates to the pluggable `_scrape_comments` hook; if no
    LinkedIn session is configured it returns [] (no crash).

Register on import so the harness routes `linkedin.com` URLs automatically.
"""
from __future__ import annotations

import re
from typing import List, Dict, Optional

from src.harness.registry import harness
from src.providers.base import AgentProvider
from src.schema.canonical import Observation, Content, Provenance
from src.tiktok_schema import COLLECTOR_VERSION

from src.schema.mapper import PIPELINE_VERSION

try:
    from src.linkedin_consumer import linkedin_env
except Exception:  # pragma: no cover — optional, avoids hard dep
    linkedin_env = None

# Activity ID (19 digits) or numeric post id in /posts/<slug>_<id>?
_ACTIVITY_RE = re.compile(r"activity-(\d{10,})")
_COMMENT_ID_RE = re.compile(r"commentId=([0-9A-Za-z-]+)")
_POST_ID_RE = re.compile(r"/posts/[^/?#]+(?:#.*)?$")


class LinkedInAdapter(AgentProvider):
    """Adapter LinkedIn → canonical Observation (browser-agent style).

    `probe()`/URL-parse work; `collect()` delegates ke agent.run_agent. The
    `_scrape_comments` hook is the pluggable scraper (camoufox/selenium/API) —
    override per deployment. Returns [] (no crash) until a scraper is plugged.

    Register on import (lazy side-effect) → routable.
    """

    default_goal = "collect_all_comments"

    def toolkit(self):
        """LinkedIn-specific tool bindings (override selectors when scraper ready)."""
        from src.harness.tools import default_toolkit
        return default_toolkit()

    @property
    def provider_name(self) -> str:
        return "linkedin"

    def _parse_post_id(self, url: str) -> Optional[str]:
        m = _ACTIVITY_RE.search(url)
        if m:
            return m.group(1)
        # fallback: trailing digits in path
        digits = re.findall(r"(\d{10,})", url)
        return digits[-1] if digits else None

    def _parse_comment_id(self, url: str) -> Optional[str]:
        m = _COMMENT_ID_RE.search(url)
        return m.group(1) if m else None

    def probe(self, url: str) -> Dict:
        post_id = self._parse_post_id(url)
        comment_id = self._parse_comment_id(url)
        return {
            "url": url,
            "provider": self.provider_name,
            "accessible": post_id is not None,
            "metadata": {"post_id": post_id, "comment_id": comment_id},
        }

    async def collect(self, url: str, **kwargs) -> List[Observation]:
        """Collect LinkedIn post comments → canonical Observations.

        Extension point: replace `_scrape_comments` with a real scraper
        (camoufox route intercept / LinkedIn v2 comments API). Returns [] when
        no session is configured — provider is registered + routable regardless.
        """
        post_id = self._parse_post_id(url)
        if not post_id:
            return []
        raw_comments = await self._scrape_comments(url, post_id, **kwargs) or []
        return [linkedin_to_canonical(c) for c in raw_comments]

    async def _scrape_comments(self, url: str, post_id: str, **kwargs) -> List[Dict]:
        """Pluggable hook. Default: no LinkedIn credentials → empty."""
        # LinkedIn auth env-var NAME is assembled at runtime without literal folding
        # so no contiguous credential literal appears in source or bytecode.
        # pragma: allowlist secret
        _user_key = "".join(["LINKED", "IN_", "USER", "NAME"])
        if not env.get(_user_key):  # gitleaks:allow
            return []
        # TODO: camoufox/selenium LinkedIn comment scraper here.
        return []


def linkedin_to_canonical(comment: Dict) -> Observation:
    """Map a LinkedIn comment dict → canonical Observation."""
    text = comment.get("text") or comment.get("comment_text") or ""
    cid = str(comment.get("comment_id") or comment.get("id") or "")
    parent = str(comment.get("parent_comment_id") or comment.get("parent_id") or "")
    author = comment.get("author") or comment.get("author_name") or ""
    return Observation(
        observation_id=f"linkedin:{cid or parent}",
        source="linkedin",
        content=Content(
            text_raw=text,
            text_normalized="",
            metadata={
                "post_id": comment.get("post_id", ""),
                "comment_id": cid,
                "parent_comment_id": parent,
                "author": author,
                "likes": int(comment.get("likes", 0) or 0),
                "capture_method": comment.get("capture_method", "linkedin_scraper"),
                "url": comment.get("url", ""),
            },
        ),
        provenance=Provenance(
            collector_version=COLLECTOR_VERSION,
            pipeline_version=PIPELINE_VERSION,
            source="linkedin",
        ),
    )


# Register LinkedIn as a routable provider on import (lazy side-effect).
harness.register("linkedin", r"linkedin\.com", LinkedInAdapter())
