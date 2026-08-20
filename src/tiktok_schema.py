#!/usr/bin/env python3
"""
TikTok Data Acquisition Engine — Schema Definitions

Lapisan data:
  raw.v1      : output langsung collector (DOM/CDP), apa adanya
  normalized.v1 : text_raw + text_normalized, parent/reply relation, context snapshot
  enriched.v1   : + identity inference, quality score, LLM annotation
  curated.v1    : final output, lolos quality gate

Provenance wajib ada di setiap level — ini jaga traceability kalau
pipeline berhenti/rollback perlu tahu dari mana data berasal.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from datetime import datetime, timezone
import json

SCHEMA_VERSION = "1.0"
COLLECTOR_VERSION = "0.4.0"

def _now() -> str:
    """ISO-8601 timestamp UTC, konsisten di semua level."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Author:
    author_id: str              # TikTok user unique ID (numeric string)
    author_handle: str         # @handle / unique_id
    display_name: str = ""


@dataclass
class RawComment:
    """Level L0 — apa adanya dari TikTok (DOM scrape atau CDP API response)."""
    schema_version: str = f"raw.v{SCHEMA_VERSION}"
    source: str = "tiktok"
    video_id: str = ""
    video_url: str = ""
    comment_id: str = ""
    parent_comment_id: str = ""      # empty string = top-level, isi = reply ke parent
    author: Author = field(default_factory=lambda: Author("", ""))
    text_raw: str = ""
    likes: int = 0
    reply_count: int = 0
    create_time: int = 0            # unix timestamp
    images: List[str] = field(default_factory=list)
    capture_method: str = ""         # "cdp" | "dom"
    captured_at: str = field(default_factory=_now)
    collector_version: str = COLLECTOR_VERSION

    # Context snapshot — karena "gue juga" tanpa konteks = not useful
    video_context: dict = field(default_factory=lambda: {
        "caption": "",
        "hashtags": [],
        "creator": "",
        "create_time": 0,
        "transcription": ""     # optional, kalau ada
    })

    def to_dict(self) -> dict:
        d = asdict(self)
        a = d.pop("author") or {}  # guard: author None (mis. dari dict partial)
        d["author_id"] = a.get("author_id", "")
        d["author_handle"] = a.get("author_handle", "")
        return d


def raw_from_api(c: dict, video_ctx: dict, method: str = "cdp", parent_comment_id: str = "") -> RawComment:
    """Convert TikTok API comment item → RawComment.

    `parent_comment_id` is injected explicitly for reply pages: TikTok sends
    reply requests to ``/comment/list/reply/?comment_id=<PARENT_CID>`` and the
    response objects do NOT carry their parent id, so collector._on_route parses
    the parent cid from the request URL and injects it here.
    """
    user = c.get("user", {}) or {}
    return RawComment(
        video_id=video_ctx.get("video_id", ""),
        video_url=video_ctx.get("video_url", ""),
        comment_id=c.get("cid", ""),
        parent_comment_id=(
            parent_comment_id
            or c.get("parent_comment_id")
            or c.get("reply_comment_id")
            or c.get("parent_cid")
            or ""
        ),
        author=Author(
            author_id=user.get("id", ""),
            author_handle=user.get("unique_id", c.get("username", "")),
            display_name=user.get("nickname", c.get("display_name", "")),
        ),
        text_raw=c.get("text", ""),
        likes=c.get("digg_count", 0),
        reply_count=c.get("reply_comment_total", 0),
        create_time=int(c.get("create_time", 0)),
        capture_method=method,
        video_context=video_ctx,
    )


def raw_from_dom(row: dict, video_ctx: dict) -> RawComment:
    """Convert DOM-scrape row → RawComment (CDP fallback)."""
    uname = row.get("username", "")
    return RawComment(
        video_id=video_ctx.get("video_id", ""),
        video_url=video_ctx.get("video_url", ""),
        comment_id=row.get("comment_id", ""),
        parent_comment_id=row.get("parent_comment_id", "") or "",
        author=Author(
            author_id="",                # DOM biasanya nggak ketahui numeric ID
            author_handle=uname,
            display_name=row.get("display_name", ""),
        ),
        text_raw=row.get("raw", ""),
        likes=row.get("likes", 0),
        reply_count=row.get("replies", 0),
        create_time=int(row.get("create_time", 0)),
        capture_method="dom",
        images=row.get("images", []),
        video_context=video_ctx,
    )


@dataclass
class NormalizedComment:
    """Level L1 — text_raw + text_normalized, relations, context."""
    schema_version: str = f"normalized.v{SCHEMA_VERSION}"
    source: str = "tiktok"
    video_id: str = ""
    video_url: str = ""
    comment_id: str = ""
    parent_comment_id: str = ""
    author_id: str = ""
    author_handle: str = ""
    display_name: str = ""
    text_raw: str = ""
    text_normalized: str = ""
    likes: int = 0
    reply_count: int = 0
    create_time: int = 0
    images: List[str] = field(default_factory=list)
    capture_method: str = ""
    captured_at: str = ""
    collector_version: str = COLLECTOR_VERSION
    normalized_at: str = field(default_factory=_now)
    normalizer_version: str = COLLECTOR_VERSION
    video_context: dict = field(default_factory=lambda: {
        "caption": "", "hashtags": [], "creator": "", "create_time": 0, "transcription": ""
    })

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_text(text: str) -> str:
    """
    Normalisasi ringan: lowercase, unicode normalization, whitespace,
    hapus variasi berlebupan.
    JANGAN hapus slang/emoji — itu signal, bukan noise.
    """
    import unicodedata
    # NFKC fold
    t = unicodedata.normalize("NFKC", text)
    # Normalisasi whitespace
    t = " ".join(t.split())
    # Lowercase
    return t.lower().strip()


def raw_to_normalized(r: RawComment) -> NormalizedComment:
    """Raw → Normalized. Hanya transformasi tekstual, tidak ada LLM."""
    a = r.author
    return NormalizedComment(
        video_id=r.video_id,
        video_url=r.video_url,
        comment_id=r.comment_id,
        parent_comment_id=r.parent_comment_id,
        author_id=a.author_id,
        author_handle=a.author_handle,
        display_name=a.display_name,
        text_raw=r.text_raw,
        text_normalized=normalize_text(r.text_raw),
        likes=r.likes,
        reply_count=r.reply_count,
        create_time=r.create_time,
        images=r.images,
        capture_method=r.capture_method,
        captured_at=r.captured_at,
        collector_version=r.collector_version,
        video_context=r.video_context,
    )


@dataclass
class EnrichedComment:
    """Level L2 — LLM inference: identity, quality score, annotation."""
    schema_version: str = f"enriched.v{SCHEMA_VERSION}"
    normalized_data: dict = field(default_factory=dict)   # snapshot NormalizedComment.to_dict()
    identity: Optional[dict] = None                       # {real_name, company, role, linkedin_hint, confidence}
    quality: Optional[dict] = None                       # {quality, semantic_density, spam_probability, ...}
    annotations: Optional[dict] = None                   # arbitrary LLM output
    provenance: dict = field(default_factory=lambda: {
        "annotator": "",
        "model": "",
        "processed_at": _now(),
    })

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CuratedComment:
    """Level L3/L4 — lolos quality gate, siap masuk RAG/gold set."""
    schema_version: str = f"curated.v{SCHEMA_VERSION}"
    enriched_data: dict = field(default_factory=dict)
    curated_at: str = field(default_factory=_now)
    curator_version: str = COLLECTOR_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


# ── JSONL Writer ──────────────────────────────────────────────────────────────

def write_jsonl(path: str, records: List[dict], append: bool = True) -> int:
    """Tulis list of dict ke JSONL. Idempotent: cek comment_id duplikat."""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # Kalau append, baca existing IDs untuk dedup di level writer
    existing_ids: set = set()
    if append and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    if "comment_id" in d:
                        existing_ids.add(d["comment_id"])
        except Exception:
            pass

    written = 0
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as f:
        for r in records:
            cid = r.get("comment_id", "")
            if cid in existing_ids:
                continue
            if cid:
                existing_ids.add(cid)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            written += 1

    return written
