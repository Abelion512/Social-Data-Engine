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
from functools import lru_cache
from typing import Optional, List
import re

# ── Provider-independent runtime primitives ──────────────────────────────────
# Pagination/retry/termination state, metrics and the termination taxonomy are
# NOT TikTok-specific: they now live in src/runtime/ and are re-exported here so
# every existing TikTok import path keeps working byte-for-byte.
from src.runtime.context import _now
from src.runtime.termination import TerminationReason
from src.runtime.metrics import AcquisitionMetrics
from src.runtime.state import PaginationState, PaginationDiagnostic
from src.runtime.dataset import append_records as _runtime_append_records

SCHEMA_VERSION = "1.1"
COLLECTOR_VERSION = "1.1.0"

__all__ = [
    "SCHEMA_VERSION", "COLLECTOR_VERSION", "_now", "TerminationReason",
    "AcquisitionMetrics", "PaginationDiagnostic", "PaginationState",
    "Author", "RawComment", "raw_from_api", "raw_from_dom",
    "NormalizedComment", "normalize_text", "raw_to_normalized",
    "EnrichedComment", "CuratedComment",
    "append_raw_records", "write_jsonl",
    "CONTENT_ID_RE", "parse_content_id", "try_parse_content_id",
]

# ── TikTok content-id parsing — SINGLE SOURCE OF TRUTH ───────────────────────
# Both URL forms resolve to the same canonical numeric id:
#   https://www.tiktok.com/@user/video/7669640839861112071
#   https://www.tiktok.com/@user/photo/7673343206544706837
# Collector, provider actor, CLI and benchmark each used to carry their own copy
# of this regex (benchmark used rsplit("/"), which breaks on `?query` URLs).
CONTENT_ID_RE = re.compile(r"/(?:video|photo)/(\d+)")


def parse_content_id(url: str) -> str:
    """Canonical numeric content id from a TikTok video/photo URL.

    Raises ``ValueError`` when the URL carries no ``/(?:video|photo)/<digits>``
    path segment. Digit-only by construction, so the result is always safe to
    use as a filename component (see ``src.runtime.context.rooted_file``).
    """
    m = CONTENT_ID_RE.search(url or "")
    if not m:
        raise ValueError(f"not a TikTok video/photo URL: {url!r}")
    return m.group(1)


def try_parse_content_id(url: str) -> str:
    """Non-raising variant: returns "" when the URL has no canonical id.

    For paths that already treat a missing id as a normal outcome (DOM scrape
    of a page whose URL changed, route interception of unrelated requests).
    """
    m = CONTENT_ID_RE.search(url or "")
    return m.group(1) if m else ""



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
    images: List[str] = field(default_factory=list)        # photo / sticker GIF URLs
    audio: List[str] = field(default_factory=list)        # voice-comment audio URLs
    sticker: Optional[str] = None                          # bila comment = sticker murni (GIF src)
    capture_method: str = ""         # "cdp" | "dom" | "api" | "route"
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
        likes=c.get("digg_count", c.get("like_count", 0)),
        reply_count=c.get("reply_comment_total", c.get("reply_count", 0)),
        create_time=int(c.get("create_time", 0)),
        # TikTok API comment objek jarang bawa foto/sticker/voice, tapi parse
        # defensif agar konsisten dengan DOM.
        images=c.get("images", []) or [],
        audio=c.get("audio", []) or [],
        sticker=c.get("sticker") or c.get("sticker_url") or None,
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
        audio=row.get("audio", []),
        sticker=row.get("sticker"),
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


@lru_cache(maxsize=50_000)
def normalize_text(text: str) -> str:
    """
    Normalisasi ringan: lowercase, unicode normalization, whitespace,
    hapus variasi berlebupan.
    JANGAN hapus slang/emoji — itu signal, bukan noise.

    Pure str→str, jadi dibungkus bounded memo cache (50k entry): pipeline yang
    sama menormalkan teks yang sama beberapa kali (normalize stage, dedup tier 2,
    bigram tier 3, manifest) dan duplikat memang umum di corpus ini — hasilnya
    identik, kerja NFKC-nya cuma sekali.
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


# ── JSONL Writer (delegates to the provider-independent runtime sink) ────────

def append_raw_records(path: str, records: List[dict], seen_ids: Optional[set] = None) -> int:
    """Atomically append new raw records to JSONL, skipping any IDs in seen_ids or already on disk.

    Guarantees incremental persistence with immediate fsync for durability.
    Thin wrapper over src.runtime.dataset.append_records (id key: comment_id).
    """
    return _runtime_append_records(path, records, seen_ids=seen_ids, id_keys=("comment_id",))


def write_jsonl(path: str, records: List[dict], append: bool = True) -> int:
    """Tulis list of dict ke JSONL. Idempotent: cek comment_id duplikat."""
    return _runtime_append_records(path, records, seen_ids=None, id_keys=("comment_id",), append=append)
