#!/usr/bin/env python3
"""
TikTok Acquisition Actor — the TikTok path migrated onto the actor contract.

This module migrates the TikTok acquisition path onto the Harness/Actor
Contract (identity, declared capabilities, RunInput, lifecycle, provenance)
WITHOUT changing any TikTok scraping semantics:

- The live browser loop (`src/collector.py::_capture_pass` + route capture +
  reply-thread expansion) remains the authoritative production page source,
  exactly as documented in docs/RUNTIME.md §G. Nothing in this module touches
  it.
- What migrates here is everything AROUND execution: TikTok's actor identity
  (actor_id/actor_version), its DECLARED capabilities (policy vocabulary,
  configuration only), and a faithful converter from TikTok's raw API page
  shape (`{"comments": [...], "cursor": N, "has_more": 0|1}`) to the generic
  `PageResult` so TikTok-shaped payloads run through the shared runtime.

Wiring status (honest): `page_source` is injected. The production
browser-backed page source is wired in a follow-up PR; until then this actor
is exercised deterministically with recorded-shaped pages
(tests/test_actor_harness.py). No claim of live verification is made here.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.policy.models import CAP_BROWSER_AUTOMATE, CAP_NETWORK_FETCH, Capability
from src.runtime.actor import AcquisitionActor, PageResult
from src.runtime.context import RunContext

# Provenance-honest versioning: the actor mirrors the collector version it
# wraps (docs/VERSIONING.md — COLLECTOR_VERSION mirrors the semver master).
from src.tiktok_schema import COLLECTOR_VERSION as _COLLECTOR_VERSION


def tiktok_api_page_to_page_result(page: Any) -> PageResult:
    """Convert one raw TikTok comments-API page into a generic PageResult.

    Recognized shape (the proven live payload shape):
        {"comments": [ {...}, ...], "cursor": <int|str>, "has_more": <0|1|bool>}

    Malformed input NEVER raises: it becomes a classified ``parse_failure``
    PageResult so the runtime's parse budget handles it like any other bad
    page. Provider quirks stop at this boundary — the runtime only ever sees
    items / cursor / has_more.
    """
    if not isinstance(page, dict):
        return PageResult(
            error=f"tiktok page: expected dict, got {type(page).__name__}",
            error_type="parse_failure",
        )
    comments = page.get("comments")
    if comments is None:
        comments = []
    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        return PageResult(
            error="tiktok page: 'comments' must be a list of dicts",
            error_type="parse_failure",
        )
    raw_has_more = page.get("has_more")
    has_more: Optional[bool] = None if raw_has_more is None else bool(raw_has_more)
    return PageResult(
        items=comments,
        next_cursor=page.get("cursor"),
        has_more=has_more,
    )


class TikTokAcquisitionActor(AcquisitionActor):
    """TikTok path on the generic actor contract.

    - identity   : actor_id='acquisition.tiktok', actor_version=COLLECTOR_VERSION
    - id space   : raw TikTok records are keyed by 'comment_id'
    - declaration: network.fetch + browser.automate (DECLARATION ONLY — these
                   names are recorded into run provenance; nothing grants or
                   denies them yet)
    - execution  : fetch_page delegates to the injected page_source and
                   converts its raw TikTok page via
                   `tiktok_api_page_to_page_result`
    """

    provider_name = "tiktok"
    id_key = "comment_id"
    actor_id = "acquisition.tiktok"
    actor_version = _COLLECTOR_VERSION

    def __init__(self, page_source: Optional[Callable[[Any, int], Dict[str, Any]]] = None):
        # Fail fast (loud, not silent): an actor without a page source cannot
        # execute; constructing it anyway would only produce classified noise.
        if page_source is None:
            raise ValueError(
                "TikTokAcquisitionActor requires a page_source(cursor, "
                "page_index) -> raw-tiktok-page callable (the production "
                "browser-backed source is wired in a follow-up PR)"
            )
        self._page_source = page_source

    def capabilities(self) -> tuple:
        return (CAP_NETWORK_FETCH, CAP_BROWSER_AUTOMATE)

    def initial_cursor(self) -> int:
        return 0

    async def fetch_page(self, ctx: RunContext, cursor, page_index: int) -> PageResult:
        return tiktok_api_page_to_page_result(self._page_source(cursor, page_index))
