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

Wiring status (honest): the REAL page source is wired through dependency
injection — `CollectorApiPageSource` delegates to the existing
`src.collector.fetch_comments_api` UNMODIFIED (same URL pattern, same-origin
cookie fetch, retry/backoff, challenge classification). Scope note, stated
plainly: `fetch_comments_api` yields cursor-paginated TOP-LEVEL comment
pages. The scroll + click-driven REPLY-THREAD EXPANSION lives inside
`_capture_pass` and is deliberately NOT part of this source — that legacy
pass remains authoritative for threaded collection and is untouched. Live
verification is environment-bound (visible browser, human-in-the-loop login
per agents.md); no live claim is made from headless runs.
"""
from __future__ import annotations

import inspect
import re
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

    Collector classification PASSES THROUGH: when the raw page carries the
    collector's own ``error`` / ``error_type`` (e.g. ``auth_blocked`` from
    challenge detection in ``fetch_comments_api``), it becomes an errored
    PageResult so the runtime terminates exactly like the legacy loop did —
    challenges are NEVER laundered into harmless empty pages.
    """
    if not isinstance(page, dict):
        return PageResult(
            error=f"tiktok page: expected dict, got {type(page).__name__}",
            error_type="parse_failure",
        )
    raw_error = page.get("error")
    if raw_error:
        # Collector already classified this page (auth_blocked, fetch_failure,
        # ...). Preserve the verdict instead of masking it as an empty page.
        return PageResult(
            error=str(raw_error)[:200],
            error_type=str(page.get("error_type") or "fetch_failure"),
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
                "page_index) -> raw-tiktok-page callable — use "
                "CollectorApiPageSource for the real collector-backed source"
            )
        self._page_source = page_source

    def capabilities(self) -> tuple:
        return (CAP_NETWORK_FETCH, CAP_BROWSER_AUTOMATE)

    def initial_cursor(self) -> int:
        return 0

    async def fetch_page(self, ctx: RunContext, cursor, page_index: int) -> PageResult:
        result = self._page_source(cursor, page_index)
        if inspect.isawaitable(result):
            # Awaitable sources are the production shape (browser I/O);
            # sync callables remain valid for deterministic recorded pages.
            result = await result
        return tiktok_api_page_to_page_result(result)


# ── Production page source (REAL collector logic, dependency-injected) ────────

def parse_tiktok_video_id(video_url: str) -> str:
    """Extract the canonical numeric id from a TikTok video/photo URL.

    Mirrors the canonical regex used by the legacy entrypoint
    (src/tiktok_linkedin.py): r"/(?:video|photo)/(\\d+)" — same semantics,
    no behavior change.
    """
    m = re.search(r"/(?:video|photo)/(\d+)", video_url or "")
    if not m:
        raise ValueError(f"not a TikTok video/photo URL: {video_url!r}")
    return m.group(1)


class CollectorApiPageSource:
    """The REAL TikTok page source: delegates to ``fetch_comments_api``.

    Reuses the existing collector primitive UNMODIFIED — identical API URL
    pattern, same-origin cookie credentials, retry/backoff, and challenge /
    auth_blocked classification. This adapter only binds arguments:

    - ``page``      : a LIVE browser page (dependency-injected; never created here)
    - ``video_id``  : canonical numeric id (see :func:`parse_tiktok_video_id`)
    - ``count``     : page size (TikTok caps at 50 — collector's own constant)
    - ``retries``   : per-page retry budget owned by the collector primitive

    Scope (documented, not hidden): cursor-paginated TOP-LEVEL comment pages.
    Reply-thread expansion stays in the legacy ``_capture_pass`` flow.
    """

    def __init__(self, page, video_id, *, count: int = 50, retries: int = 3):
        if page is None:
            raise ValueError(
                "CollectorApiPageSource requires a live browser page — "
                "connect one via connect_production_page() (human-in-the-loop login)"
            )
        vid = str(video_id or "")
        if not vid.isdigit():
            raise ValueError(f"video_id must be numeric, got {video_id!r}")
        self._page = page
        self.video_id = vid
        self.count = int(count)
        self.retries = int(retries)

    async def __call__(self, cursor, page_index=0):
        # Lazy import: src.collector pulls browser-session deps at module
        # level; deterministic contract tests must not require them.
        from src.collector import fetch_comments_api

        try:
            cur = int(cursor)
        except (TypeError, ValueError):
            cur = 0
        # page_index is accepted for the actor contract but unused: this
        # source is cursor-driven, exactly like the legacy loop.
        return await fetch_comments_api(
            self._page, self.video_id,
            cursor=cur, count=self.count, retries=self.retries,
        )


async def connect_production_page(video_url: str, *, force_camoufox: bool = True):
    """Connect the production browser session for one TikTok target.

    Thin wrapper over the existing BrowserSession flow (same as the legacy
    loop / harness agents). Login and captcha remain HUMAN-IN-THE-LOOP per
    agents.md — this function never authenticates on anyone's behalf.
    Returns ``(session, video_id)``; the CALLER owns the session lifecycle
    (visible-browser sessions must stay open for interactive flows).
    """
    from src.browser_selector import BrowserSession  # lazy: production-only deps

    video_id = parse_tiktok_video_id(video_url)
    session = BrowserSession()
    ok = await session.connect(force_camoufox=force_camoufox)
    if not ok or getattr(session, "page", None) is None:
        raise RuntimeError(
            "production TikTok page source requires a connected browser "
            "(desktop visible-browser flow per agents.md)"
        )
    return session, video_id


async def run_live(video_url: str, *, max_items: int = 200, max_pages=None,
                   count: int = 50, force_camoufox: bool = True, job_id=None,
                   state_dir: str = "state/runs", data_dir: str = "data/runs",
                   print_fn=print):
    """Run the REAL TikTok actor through ActorHarness against a live target.

    Proves the full chain: URL → video_id → connected page →
    CollectorApiPageSource → TikTokAcquisitionActor → ActorHarness →
    AcquisitionRuntime (budgets, checkpoint, provenance). Returns
    ``(RunSummary, session)``; caller owns the session lifecycle.
    """
    from src.runtime.harness import ActorHarness, RunInput  # lazy: avoid import cycles

    session, video_id = await connect_production_page(
        video_url, force_camoufox=force_camoufox)
    try:
        actor = TikTokAcquisitionActor(
            page_source=CollectorApiPageSource(session.page, video_id, count=count))
        # S-G1 part 1 (threat model §3.D item 3): the harness DEFAULT is now a
        # deny-all profile. The legacy CLI/live path predates gate unification,
        # so it opts out via the explicit keyword-only trusted_operator switch —
        # the documented Phase 2 window escape hatch (removed at Phase 3 CLI
        # unification; tracked as TM-02/TM-24 residual).
        harness = ActorHarness(print_fn=print_fn, state_dir=state_dir, data_dir=data_dir,
                               trusted_operator=True)
        config = {"max_items": int(max_items)}
        if max_pages is not None:
            config["max_pages"] = int(max_pages)
        run_input = RunInput(
            actor_id=actor.actor_id,
            actor_version=actor.actor_version,
            provider=actor.provider_name,
            target_url=video_url,
            payload={"video_id": video_id},
            config=config,
            job_id=job_id,
        )
        summary = await harness.run(actor, run_input)
    except BaseException:
        # The session was already connected but never handed back to the
        # caller (returned only on success) — detach it here, otherwise a
        # failed/cancelled run leaks a live browser. close() is attach-safe:
        # it never terminates the operator's own browser (CDP detaches).
        await session.close()
        raise
    return summary, session
