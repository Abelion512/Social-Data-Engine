#!/usr/bin/env python3
"""AcquisitionMetrics — provider-independent structured telemetry (moved verbatim
from src/tiktok_schema.py; serialization is unchanged so existing checkpoints load)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from src.runtime.context import utc_now

_now = utc_now


@dataclass
class AcquisitionMetrics:
    """Structured telemetry for long-running acquisition jobs."""
    pages_attempted: int = 0
    pages_succeeded: int = 0
    items_collected: int = 0
    items_unique: int = 0
    items_deduplicated: int = 0
    fetch_errors: int = 0
    parse_errors: int = 0
    empty_pages: int = 0
    stalls: int = 0
    auth_blocks: int = 0
    started_at: str = field(default_factory=_now)
    last_success_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AcquisitionMetrics":
        if not d:
            return cls()
        return cls(
            pages_attempted=d.get("pages_attempted", 0),
            pages_succeeded=d.get("pages_succeeded", 0),
            items_collected=d.get("items_collected", 0),
            items_unique=d.get("items_unique", 0),
            items_deduplicated=d.get("items_deduplicated", 0),
            fetch_errors=d.get("fetch_errors", 0),
            parse_errors=d.get("parse_errors", 0),
            empty_pages=d.get("empty_pages", 0),
            stalls=d.get("stalls", 0),
            auth_blocks=d.get("auth_blocks", 0),
            started_at=d.get("started_at", _now()),
            last_success_at=d.get("last_success_at"),
            ended_at=d.get("ended_at"),
            duration_seconds=float(d.get("duration_seconds", 0.0)),
        )
