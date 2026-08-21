#!/usr/bin/env python3
"""RunContext — provider-independent identity & location for one acquisition run."""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> str:
    """ISO-8601 UTC timestamp — single clock for the whole runtime."""
    return datetime.now(timezone.utc).isoformat()


# Backward-compatible alias: src/tiktok_schema.py historically exposed `_now`.
_now = utc_now


@dataclass
class RunContext:
    """Everything the runtime needs to identify, persist and resume one run.

    The actor only *reads* this; the runtime owns its lifecycle.
    """
    run_id: str
    job_id: str
    provider: str
    target: Dict[str, Any] = field(default_factory=dict)   # url + input metadata
    started_at: str = field(default_factory=utc_now)
    checkpoint_path: str = ""
    dataset_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(
        cls,
        provider: str,
        target_url: str,
        job_id: Optional[str] = None,
        run_id: Optional[str] = None,
        state_dir: str = "state/runs",
        data_dir: str = "data/runs",
        target_meta: Optional[Dict[str, Any]] = None,
    ) -> "RunContext":
        """Factory: derive stable job identity + default durable locations."""
        jid = job_id or f"{provider}_{int(time.time() * 1000)}"
        rid = run_id or uuid4_str()
        target: Dict[str, Any] = {"url": target_url}
        if target_meta:
            target.update(target_meta)
        return cls(
            run_id=rid,
            job_id=jid,
            provider=provider,
            target=target,
            started_at=utc_now(),
            checkpoint_path=f"{state_dir.rstrip('/')}/{jid}.json",
            dataset_path=f"{data_dir.rstrip('/')}/{jid}.jsonl",
        )


def uuid4_str() -> str:
    import uuid
    return str(uuid.uuid4())
