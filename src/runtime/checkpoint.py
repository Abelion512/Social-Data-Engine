#!/usr/bin/env python3
"""CheckpointStore — atomic single-file JSON checkpoint (tmp write + replace)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class CheckpointStore:
    """Durable, atomic checkpoint for one run.

    `save()` writes to `<path>.tmp` then atomically replaces the target, so a
    crash mid-write never leaves a torn checkpoint. Callers must only commit a
    checkpoint AFTER the corresponding dataset writes succeeded — that
    ordering is enforced by AcquisitionRuntime and mirrors the TikTok
    collector's hardening invariant.
    """

    def __init__(self, path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None
