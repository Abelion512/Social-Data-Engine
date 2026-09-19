#!/usr/bin/env python3
"""RunContext — provider-independent identity & location for one acquisition run.

Security (S-G2, threat model §3.D): run identity is slug-safe and every
durable path is resolved STRICTLY inside its workspace root. A hostile
identifier can never move a checkpoint/dataset write outside the workspace —
the rooted-path assertion raises BEFORE any filesystem mutation happens
(``create`` performs no writes itself; the check guards everything downstream).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.policy.models import is_valid_identifier


def utc_now() -> str:
    """ISO-8601 UTC timestamp — single clock for the whole runtime."""
    return datetime.now(timezone.utc).isoformat()


# Backward-compatible alias: src/tiktok_schema.py historically exposed `_now`.
_now = utc_now

_SLUG_DOC = ("a slug identifier (^[a-z0-9][a-z0-9._-]{0,127}$ — lowercase "
             "alphanumeric start, then alphanumerics/dots/underscores/hyphens)")


def require_slug_identifier(value, field_name: str) -> str:
    """Fail closed unless ``value`` is an identity-safe slug identifier.

    Shared S-G2 rule (threat model §3.D item 1): reuses
    ``src.policy.models.is_valid_identifier`` semantics exactly. The error
    always names the offending field.
    """
    if not is_valid_identifier(value):
        raise ValueError(
            f"{field_name} must be {_SLUG_DOC}, got {value!r}"
        )
    return value


def write_private_text(path, text: str) -> str:
    """Write ``text`` to ``path`` with owner-only permissions (0600).

    Asset handling (threat model §3.D / TM-19): exported session cookies are
    full account credentials, so they must never land on disk with the default
    umask-derived mode (0644 → world-readable). The parent directory is created
    with mode 0700, the file is opened with 0600, and the mode is re-asserted
    afterwards so a pre-existing looser file is tightened rather than reused.

    Stdlib only; returns the resolved path as a string.
    """
    p = Path(path).expanduser()
    parent_missing = not p.parent.exists()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent_missing:
        # Harden only the directory this call created — an existing parent is
        # the caller's, and re-permissioning it is not our business.
        os.chmod(str(p.parent), 0o700)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(str(p), 0o600)
    return str(p)


def resolve_rooted(root: str, filename: str, label: str) -> str:
    """Resolve ``filename`` under workspace ``root`` and refuse escape (S-G2).

    Threat model §3.D item 2: resolve the FINAL path and assert
    ``os.path.commonpath([resolved, root]) == root``; any violation raises
    before any write can occur. Also refuses cross-device/relative weirdness
    by treating a ``commonpath`` failure as a violation (fail closed).
    """
    root_abs = os.path.abspath(root)
    resolved = os.path.abspath(os.path.join(root_abs, filename))
    try:
        escaped = os.path.commonpath([resolved, root_abs]) != root_abs
    except ValueError:  # e.g. mixed absolute/relative or different drives
        escaped = True
    if escaped:
        raise ValueError(
            f"{label}: resolved path {resolved!r} escapes its workspace root "
            f"{root_abs!r} (hostile identifier or out-of-root location refused)"
        )
    return resolved


def rooted_file(root, filename: str, label: str = "path"):
    """Path object for ``filename`` under ``root``, refusing escape (S-G2).

    Thin Path-returning wrapper over :func:`resolve_rooted` so writers can be
    written as

        vid = require_slug_identifier(video_id, "video_id")
        out = rooted_file(DATA_DIR / "curated" / date, f"{vid}.jsonl", "curated_file")

    instead of interpolating an unvalidated identifier straight into a path
    (tm-13 class: `--video ../../../etc/passwd`).
    """
    return Path(resolve_rooted(str(root), filename, label))


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
        """Factory: derive stable job identity + default durable locations.

        Fail-closed ordering (S-G2): identifiers are slug-validated FIRST,
        then every durable path is rooted-resolved — all of this raises
        before this context could ever reach a writer.
        """
        # Identity-safe identifiers (defense in depth with RunInput validation:
        # the raw runtime accepts legacy contexts whose ids were never screened).
        if job_id is not None:
            require_slug_identifier(job_id, "job_id")
        if run_id is not None:
            require_slug_identifier(run_id, "run_id")
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
            checkpoint_path=resolve_rooted(state_dir, f"{jid}.json",
                                           "checkpoint_path (state_dir)"),
            dataset_path=resolve_rooted(data_dir, f"{jid}.jsonl",
                                        "dataset_path (data_dir)"),
        )


def uuid4_str() -> str:
    import uuid
    return str(uuid.uuid4())
