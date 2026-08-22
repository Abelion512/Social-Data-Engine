#!/usr/bin/env python3
"""
AcquisitionActor — the minimal provider/actor contract.

A future platform implements ONLY this; it never touches checkpointing,
retry budgets, metrics, persistence ordering, or termination semantics.
The contract is intentionally small (avoid over-generalization):

    - actor_id       : namespaced identity for provenance ("acquisition.tiktok")
    - actor_version  : version of THIS actor implementation (run metadata)
    - provider_name  : platform identity for provenance/routing
    - id_key         : which record field uniquely identifies an item
    - capabilities() : DECLARED Capabilities (src/policy vocabulary) —
                       declaration/configuration ONLY. Nothing evaluates,
                       grants or denies them yet; a declared capability is
                       not a granted one (Constitution §5 — DOCUMENTED ONLY).
    - initial_cursor : starting cursor (default 0)
    - fetch_page     : fetch ONE page for a cursor → PageResult
    - item_id        : extract the unique id from one raw item

Anything not expressible here (multi-path browser capture, auth flows,
payload parsing quirks) is provider code by definition and stays out of
the runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from src.policy.models import Capability
from src.runtime.context import RunContext


@dataclass
class PageResult:
    """One page outcome. Exactly one of (items / error) is meaningful.

    error_type uses the shared vocabulary understood by PaginationState:
      "fetch_failure" | "parse_failure" | "parse" | "auth" | "auth_blocked" ...
    """
    items: List[dict] = field(default_factory=list)
    next_cursor: Union[int, str, None] = None
    has_more: Optional[bool] = None          # None is treated as False by the runtime
    error: Optional[str] = None
    error_type: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class AcquisitionActor:
    """Base class for provider-specific acquisition behavior."""

    provider_name: str = "unknown"
    id_key: str = "item_id"
    # Identity defaults are EMPTY so pre-contract actors keep working through
    # the raw runtime. The ActorHarness treats empty identity as invalid
    # (fail-closed); the raw runtime merely records what is declared.
    actor_id: str = ""
    actor_version: str = ""

    def capabilities(self) -> Tuple[Capability, ...]:
        """Declared capabilities — policy VOCABULARY only, never evaluated.

        Default is the empty declaration: an actor may declare zero
        capabilities and still run (nothing consumes declarations yet).
        Override with the exact `Capability` constants the actor's provider
        behavior requires; duplicates are a declaration bug and are rejected
        by the harness.
        """
        return ()

    def initial_cursor(self) -> Union[int, str]:
        return 0

    async def fetch_page(
        self, ctx: RunContext, cursor: Union[int, str], page_index: int
    ) -> PageResult:
        """Fetch a single page of items starting at `cursor`.

        Implementations must NOT retry internally — the runtime owns the
        retry budget. Raise or return PageResult(error=...) on failure;
        both are handled identically.
        """
        raise NotImplementedError

    def item_id(self, item: dict) -> str:
        v = item.get(self.id_key, "")
        return str(v) if v else ""
