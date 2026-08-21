#!/usr/bin/env python3
"""
JsonlDataset — incremental, id-deduplicating append-only event sink.

Generalizes the TikTok raw writer (`append_raw_records`/`write_jsonl` in
src/tiktok_schema.py): same durability guarantees (append + fsync per batch,
disk-backed id dedup) with a configurable id key instead of hardcoded
`comment_id`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set

# TikTok records use "comment_id"; generic runs default to "item_id".
DEFAULT_ID_KEYS = ("item_id", "comment_id")


def load_seen_ids(path, id_keys: Sequence[str] = DEFAULT_ID_KEYS) -> Set[str]:
    """Scan an existing dataset file and return the set of non-empty item ids."""
    seen: Set[str] = set()
    p = Path(path)
    if not p.exists():
        return seen
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                for k in id_keys:
                    v = d.get(k, "")
                    if v:
                        seen.add(str(v))
                        break
    except Exception:
        pass
    return seen


class JsonlDataset:
    """Append-only JSONL sink with in-memory + disk-backed dedup by item id."""

    def __init__(self, path, id_keys: Sequence[str] = DEFAULT_ID_KEYS,
                 seen_ids: Optional[Set[str]] = None):
        self.path = Path(path)
        self.id_keys = tuple(id_keys) or DEFAULT_ID_KEYS
        self.seen_ids: Set[str] = seen_ids if seen_ids is not None else set()

    def _extract_id(self, record: dict) -> str:
        for k in self.id_keys:
            v = record.get(k, "")
            if v:
                return str(v)
        return ""

    def load_seen(self) -> int:
        """Rebuild the id index from disk (used on resume). Returns count."""
        self.seen_ids = load_seen_ids(self.path, self.id_keys)
        return len(self.seen_ids)

    def append(self, records: Iterable[dict]) -> int:
        """Append new records, skipping ids already present (memory or disk).

        Records without any id are always written (matches legacy behavior).
        Durably flushes + fsyncs before returning.
        """
        os.makedirs(str(self.path.parent) or ".", exist_ok=True)
        written = 0
        with open(self.path, "a", encoding="utf-8") as f:
            for r in records:
                rid = self._extract_id(r)
                if rid and rid in self.seen_ids:
                    continue
                if rid:
                    self.seen_ids.add(rid)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                written += 1
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        return written

    def count_lines(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())


def append_records(path, records, seen_ids: Optional[Set[str]] = None,
                   id_keys: Sequence[str] = DEFAULT_ID_KEYS,
                   append: bool = True) -> int:
    """Functional form kept for backward compatibility with tiktok_schema writers.

    - append=True  : load existing ids from disk when `seen_ids` is None.
    - append=False : truncate the file; no disk-backed dedup (fresh output).
    """
    if not append:
        p = Path(path)
        if p.exists():
            p.unlink()
        ds = JsonlDataset(path, id_keys=id_keys, seen_ids=set())
        return ds.append(records)
    ds = JsonlDataset(path, id_keys=id_keys, seen_ids=seen_ids)
    if seen_ids is None:
        ds.load_seen()
    return ds.append(records)
