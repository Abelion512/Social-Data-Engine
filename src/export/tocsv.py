#!/usr/bin/env python3
"""
CSV Export — TikTok comments → table view (termasuk nested reply, sticker,
photo, voice comment, caption / hashtags / creator context).

Output kolom konsisten, siap dibuka di spreadsheet / pandas. Nested-reply
direpresentasikan lewat `parent_comment_id` + `depth` (0 = top-level).

Usage:
    from src.export.tocsv import write_csv, csv_from_jsonl
    write_csv(records_or_jsonl_path, "out.csv")
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import List, Union, Iterable, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent

# Kolom CSV — order konsisten (context video flatten ke samping).
CSV_COLUMNS = [
    "schema_version", "source", "video_id", "video_url",
    "comment_id", "parent_comment_id", "depth", "is_reply",
    "author_id", "author_handle", "display_name",
    "text_raw", "text_normalized",
    "likes", "reply_count", "create_time",
    "images", "audio", "sticker", "image_count", "audio_count",
    "capture_method", "capture_source", "captured_at", "collector_version",
    "caption", "hashtags", "creator", "video_create_time",
    # provenance ekstra pada export
    "exported_at",
]


def _records_from_jsonl(path: Union[str, Path]) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _flatten(r: dict) -> dict:
    """Flatten satu RawComment dict → CSV row dict (semua kolom pasti ada)."""
    ctx = r.get("video_context") or {}
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except Exception:
            ctx = {}
    imgs = r.get("images") or []
    audio = r.get("audio") or []
    # depth: reply = 1, top-level = 0. (thread lebih dalam di-reconstruct di
    # normalized layer; di CSV cukup 2-level: top vs reply.)
    is_reply = bool(r.get("parent_comment_id"))
    return {
        "schema_version": r.get("schema_version", ""),
        "source": r.get("source", "tiktok"),
        "video_id": r.get("video_id", ""),
        "video_url": r.get("video_url", ""),
        "comment_id": r.get("comment_id", ""),
        "parent_comment_id": r.get("parent_comment_id", ""),
        "depth": 1 if is_reply else 0,
        "is_reply": "yes" if is_reply else "no",
        "author_id": r.get("author_id", ""),
        "author_handle": r.get("author_handle", r.get("author", {}).get("author_handle", "")),
        "display_name": r.get("display_name", r.get("author", {}).get("display_name", "")),
        "text_raw": r.get("text_raw", ""),
        "text_normalized": r.get("text_normalized", ""),
        "likes": r.get("likes", 0) or 0,
        "reply_count": r.get("reply_count", 0) or 0,
        "create_time": r.get("create_time", 0) or 0,
        "images": ";".join(imgs) if imgs else "",
        "audio": ";".join(audio) if audio else "",
        "sticker": r.get("sticker") or "",
        "image_count": len(imgs) if imgs else 0,
        "audio_count": len(audio) if audio else 0,
        "capture_method": r.get("capture_method", ""),
        "capture_source": r.get("capture_method", ""),
        "captured_at": r.get("captured_at", ""),
        "collector_version": r.get("collector_version", ""),
        "caption": ctx.get("caption", "") if ctx else "",
        "hashtags": ";".join(ctx.get("hashtags", []) or []) if ctx else "",
        "creator": ctx.get("creator", "") if ctx else "",
        "video_create_time": ctx.get("create_time", 0) if ctx else 0,
        "exported_at": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_dict(rec: Union[dict, object]) -> dict:
    if isinstance(rec, dict):
        return rec
    # dataclass (RawComment) → asdict
    from dataclasses import asdict
    try:
        return asdict(rec)  # type: ignore[attr-defined]
    except TypeError:
        return rec.__dict__ if hasattr(rec, "__dict__") else {}


def write_csv(records_or_jsonl: Union[str, Path, List[dict], List[object]],
              out_path: Union[str, Path],
              extra_meta: Optional[dict] = None) -> str:
    """Tulis records → CSV. `records_or_jsonl` boleh jalur .jsonl ATAU list of dict."""
    if isinstance(records_or_jsonl, (str, Path)):
        rows = [_flatten(_to_dict(r)) for r in _records_from_jsonl(records_or_jsonl)]
    else:
        rows = [_flatten(_to_dict(r)) for r in (records_or_jsonl or [])]
    if extra_meta:
        for row in rows:
            for k, v in extra_meta.items():
                row.setdefault(k, v)
    os.makedirs(os.path.dirname(str(out_path)) or str(_ROOT), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return str(out_path)


def csv_from_jsonl(jsonl_path: Union[str, Path],
                   csv_path: Optional[Union[str, Path]] = None,
                   extra_meta: Optional[dict] = None) -> str:
    """Konversi satu file JSONL → CSV (path default sama, .csv)."""
    jsonl_path = Path(jsonl_path)
    if csv_path is None:
        csv_path = jsonl_path.with_suffix(".csv")
    return write_csv(str(jsonl_path), str(csv_path), extra_meta)
