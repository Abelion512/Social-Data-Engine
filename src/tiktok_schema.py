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
from typing import Optional, List, Union, Dict, Any
from datetime import datetime, timezone
import json

SCHEMA_VERSION = "1.1"
COLLECTOR_VERSION = "1.1.0"

def _now() -> str:
    """ISO-8601 timestamp UTC, konsisten di semua level."""
    return datetime.now(timezone.utc).isoformat()


class TerminationReason:
    NORMAL_COMPLETION = "normal_completion"
    FETCH_FAILURE = "fetch_failure"
    PARSE_FAILURE = "parse_failure"
    EMPTY_PAGE = "empty_page"
    PAGINATION_STALL = "pagination_stall"
    AUTH_BLOCKED = "auth_blocked"
    MAX_CAP_REACHED = "max_cap_reached"


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


@dataclass
class PaginationDiagnostic:
    """Structured diagnostic captured for every pagination attempt."""
    attempt_index: int
    page_index: int
    current_cursor: Union[int, str, None]
    next_cursor: Union[int, str, None]
    response_status: Union[int, str]
    response_item_count: int
    has_more: Optional[bool]
    unique_before: int
    unique_after: int
    total_unique_comments: int
    retry_count: int
    termination_reason: Optional[str]
    source: str = "api"  # "api", "route", "dom"
    request_url_pattern: str = ""
    timestamp: str = field(default_factory=_now)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PaginationState:
    """Explicit pagination state & checkpoint tracking for comment acquisition."""
    cursor: Union[int, str] = 0
    page_index: int = 0          # alias: page / index
    items_seen: int = 0
    retry_count: int = 0
    has_more: bool = True
    termination_reason: Optional[str] = None
    consecutive_empty: int = 0
    consecutive_stalls: int = 0
    consecutive_parse_errors: int = 0
    max_retries: int = 3
    max_empty_retries: int = 5
    max_stalls: int = 3
    max_parse_retries: int = 3
    updated_at: str = field(default_factory=_now)
    metrics: AcquisitionMetrics = field(default_factory=AcquisitionMetrics)
    diagnostics: List[PaginationDiagnostic] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.page_index

    @property
    def index(self) -> int:
        return self.page_index

    def process_page(
        self,
        comments: Optional[list] = None,
        next_cursor: Union[int, str, None] = None,
        has_more: Union[int, bool, None] = None,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        deduplicated: int = 0,
    ) -> None:
        """Update pagination state after receiving an API/route page response."""
        self.updated_at = _now()
        self.metrics.pages_attempted += 1

        if error or error_type:
            err_type_str = (error_type or "").lower()
            err_str = (error or "").lower()
            if "parse" in err_type_str or "parse" in err_str:
                self.record_parse_error(error or "JSON decode / parse failure")
            elif "auth" in err_type_str or "block" in err_type_str or any(k in err_str for k in ("login", "verify", "captcha", "block", "security")):
                self.record_auth_block(error or "Authentication or anti-bot challenge")
            else:
                self.record_error(error or "Fetch / network error", error_type=error_type)
            return

        has_more_bool = bool(has_more) if has_more is not None else False

        if not has_more_bool:
            self.has_more = False
            self.termination_reason = "has_more_false"
            if next_cursor is not None:
                self.cursor = next_cursor
            if comments:
                new_items = len(comments)
                self.items_seen += new_items
                self.metrics.items_collected += (new_items + deduplicated)
                self.metrics.items_unique += new_items
                self.metrics.items_deduplicated += deduplicated
                self.metrics.pages_succeeded += 1
                self.metrics.last_success_at = self.updated_at
            return

        # has_more is True
        if comments:
            # Comments returned on this page
            new_items = len(comments)
            self.items_seen += new_items
            self.metrics.items_collected += (new_items + deduplicated)
            self.metrics.items_unique += new_items
            self.metrics.items_deduplicated += deduplicated
            self.metrics.pages_succeeded += 1
            self.metrics.last_success_at = self.updated_at

            if next_cursor is not None and next_cursor != self.cursor:
                self.cursor = next_cursor
                self.consecutive_stalls = 0
                self.consecutive_empty = 0
                self.consecutive_parse_errors = 0
                self.retry_count = 0
                self.page_index += 1
            elif next_cursor is not None and next_cursor == self.cursor:
                # Same cursor returned
                self.consecutive_stalls += 1
                self.metrics.stalls += 1
                if self.consecutive_stalls >= self.max_stalls:
                    self.has_more = False
                    self.termination_reason = "cursor_stalled"
            else:
                self.page_index += 1
                self.consecutive_empty = 0
                self.consecutive_parse_errors = 0
                self.retry_count = 0
        else:
            # Empty comments page with has_more=True (transient empty page)
            self.consecutive_empty += 1
            self.metrics.empty_pages += 1
            if next_cursor is not None and next_cursor != self.cursor:
                self.cursor = next_cursor
            if self.consecutive_empty >= self.max_empty_retries:
                self.has_more = False
                self.termination_reason = "max_empty_pages_exceeded"
            else:
                self.has_more = True

    def record_error(self, error: str = "", error_type: Optional[str] = None) -> None:
        """Record transient fetch error / challenge."""
        if error_type == "parse" or "parse" in error.lower():
            self.record_parse_error(error)
            return
        if error_type == "auth" or any(k in error.lower() for k in ("login", "verify", "captcha", "block")):
            self.record_auth_block(error)
            return

        self.updated_at = _now()
        self.retry_count += 1
        self.metrics.fetch_errors += 1
        if self.retry_count >= self.max_retries:
            self.has_more = False
            self.termination_reason = "max_retries_exceeded"

    def record_parse_error(self, error: str = "") -> None:
        """Record payload parse / JSON decoding error."""
        self.updated_at = _now()
        self.consecutive_parse_errors += 1
        self.metrics.parse_errors += 1
        if self.consecutive_parse_errors >= self.max_parse_retries:
            self.has_more = False
            self.termination_reason = "parse_failure"

    def record_auth_block(self, reason: str = "") -> None:
        """Record authentication, CAPTCHA, or anti-bot challenge."""
        self.updated_at = _now()
        self.metrics.auth_blocks += 1
        self.has_more = False
        self.termination_reason = "auth_blocked"

    def record_items(self, total_seen: int) -> None:
        """Record items seen count."""
        self.items_seen = total_seen
        self.metrics.items_unique = total_seen

    def check_cap(self, max_comments: int) -> bool:
        """Check if max_comments cap is reached."""
        if self.items_seen >= max_comments:
            self.has_more = False
            self.termination_reason = "max_comments_reached"
            return True
        return False

    def record_diagnostic(
        self,
        request_url_pattern: str,
        current_cursor: Union[int, str, None],
        response_status: Union[int, str],
        response_item_count: int,
        has_more: Optional[bool],
        next_cursor: Union[int, str, None],
        unique_before: int,
        unique_after: int,
        total_unique_comments: int,
        retry_count: int,
        termination_reason: Optional[str],
        source: str = "api",
        page_index: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> PaginationDiagnostic:
        """Capture structured telemetry for every pagination attempt."""
        diag = PaginationDiagnostic(
            attempt_index=len(self.diagnostics) + 1,
            page_index=self.page_index if page_index is None else page_index,
            current_cursor=current_cursor,
            next_cursor=next_cursor,
            response_status=response_status,
            response_item_count=response_item_count,
            has_more=has_more,
            unique_before=unique_before,
            unique_after=unique_after,
            total_unique_comments=total_unique_comments,
            retry_count=retry_count,
            termination_reason=termination_reason,
            source=source,
            request_url_pattern=request_url_pattern,
            extra=extra or {},
        )
        self.diagnostics.append(diag)
        print(f"[DIAGNOSTIC] #{diag.attempt_index} page={diag.page_index} src={diag.source} "
              f"cur_sent={diag.current_cursor} status={diag.response_status} items={diag.response_item_count} "
              f"has_more={diag.has_more} next_cur={diag.next_cursor} uniq_before={diag.unique_before} "
              f"uniq_after={diag.unique_after} total_unique={diag.total_unique_comments} "
              f"retries={diag.retry_count} term_reason={diag.termination_reason} url={diag.request_url_pattern[:80]}")
        return diag

    def to_dict(self) -> dict:
        return {
            "cursor": self.cursor,
            "page_index": self.page_index,
            "page": self.page_index,
            "index": self.page_index,
            "items_seen": self.items_seen,
            "retry_count": self.retry_count,
            "has_more": self.has_more,
            "termination_reason": self.termination_reason,
            "consecutive_empty": self.consecutive_empty,
            "consecutive_stalls": self.consecutive_stalls,
            "consecutive_parse_errors": self.consecutive_parse_errors,
            "max_retries": self.max_retries,
            "max_empty_retries": self.max_empty_retries,
            "max_stalls": self.max_stalls,
            "max_parse_retries": self.max_parse_retries,
            "updated_at": self.updated_at,
            "metrics": self.metrics.to_dict(),
            "diagnostics": [d.to_dict() if hasattr(d, "to_dict") else d for d in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PaginationState":
        if not d:
            return cls()
        metrics_raw = d.get("metrics") or {}
        raw_diags = d.get("diagnostics", [])
        parsed_diags = []
        for rd in raw_diags:
            if isinstance(rd, dict):
                try:
                    parsed_diags.append(PaginationDiagnostic(**rd))
                except Exception:
                    pass
            elif isinstance(rd, PaginationDiagnostic):
                parsed_diags.append(rd)
        return cls(
            cursor=d.get("cursor", 0),
            page_index=d.get("page_index", d.get("page", d.get("index", 0))),
            items_seen=d.get("items_seen", d.get("comments_seen", 0)),
            retry_count=d.get("retry_count", 0),
            has_more=bool(d.get("has_more", True)),
            termination_reason=d.get("termination_reason"),
            consecutive_empty=d.get("consecutive_empty", 0),
            consecutive_stalls=d.get("consecutive_stalls", 0),
            consecutive_parse_errors=d.get("consecutive_parse_errors", 0),
            max_retries=d.get("max_retries", 3),
            max_empty_retries=d.get("max_empty_retries", 5),
            max_stalls=d.get("max_stalls", 3),
            max_parse_retries=d.get("max_parse_retries", 3),
            updated_at=d.get("updated_at", _now()),
            metrics=AcquisitionMetrics.from_dict(metrics_raw),
            diagnostics=parsed_diags,
        )



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

def append_raw_records(path: str, records: List[dict], seen_ids: Optional[set] = None) -> int:
    """Atomically append new raw records to JSONL, skipping any IDs in seen_ids or already on disk.
    
    Guarantees incremental persistence with immediate fsync for durability.
    """
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if seen_ids is None:
        seen_ids = set()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        if "comment_id" in d:
                            seen_ids.add(d["comment_id"])
            except Exception:
                pass

    written = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            cid = r.get("comment_id", "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            written += 1
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    return written


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
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    return written
