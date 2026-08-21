#!/usr/bin/env python3
"""
PaginationState — provider-independent pagination / retry / termination state.

Moved verbatim from src/tiktok_schema.py (semantics, serialization and legacy
reason strings unchanged so existing TikTok checkpoints keep loading). The
field name `comments` in `process_page` is historical TikTok vocabulary; the
runtime treats it as generic "items on a page".
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Union

from src.runtime.context import utc_now
from src.runtime.metrics import AcquisitionMetrics

_now = utc_now


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
    """Explicit pagination state & checkpoint tracking for item acquisition."""
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
        """Check if the configured item cap is reached.

        Note: `max_comments`/`max_comments_reached` are legacy names kept for
        byte-compatible checkpoints; semantically this is the run's item cap.
        """
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
