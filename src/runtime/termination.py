#!/usr/bin/env python3
"""
Termination taxonomy — provider-independent.

`TerminationReason` is the *raw* reason string recorded in checkpoints
(values are kept byte-compatible with existing TikTok checkpoints on disk).
`Outcome` classifies each raw reason into one of seven semantic categories
requested by the runtime contract:
success / retryable failure / permanent failure / auth-block /
pagination stall / normal completion / user-or-configured cap.
"""
from __future__ import annotations


class TerminationReason:
    NORMAL_COMPLETION = "normal_completion"
    FETCH_FAILURE = "fetch_failure"
    PARSE_FAILURE = "parse_failure"
    EMPTY_PAGE = "empty_page"
    PAGINATION_STALL = "pagination_stall"
    AUTH_BLOCKED = "auth_blocked"
    MAX_CAP_REACHED = "max_cap_reached"


class Outcome:
    """Semantic classification of a run's end state."""
    SUCCESS = "success"                        # normal completion (incl. has_more_false)
    RETRYABLE_FAILURE = "retryable_failure"    # transient budget exhausted — may be re-run
    PERMANENT_FAILURE = "permanent_failure"    # unknown/unclassified terminal failure
    AUTH_BLOCKED = "auth_blocked"              # login/captcha/anti-bot challenge
    PAGINATION_STALL = "pagination_stall"      # cursor stopped advancing
    CAP_REACHED = "cap_reached"                # user/configured item or page cap

    ALL = (SUCCESS, RETRYABLE_FAILURE, PERMANENT_FAILURE, AUTH_BLOCKED,
           PAGINATION_STALL, CAP_REACHED)


# reason string → outcome category. Legacy TikTok strings are first-class here
# so existing checkpoints classify without migration.
_REASON_TO_OUTCOME = {
    # success / normal completion
    "has_more_false": Outcome.SUCCESS,
    "normal_completion": Outcome.SUCCESS,
    "finished": Outcome.SUCCESS,
    # auth / block
    "auth_blocked": Outcome.AUTH_BLOCKED,
    # pagination stall
    "cursor_stalled": Outcome.PAGINATION_STALL,
    "pagination_stall": Outcome.PAGINATION_STALL,
    # caps (legacy TikTok string preserved for checkpoint compatibility)
    "max_comments_reached": Outcome.CAP_REACHED,
    "max_cap_reached": Outcome.CAP_REACHED,
    "max_items_reached": Outcome.CAP_REACHED,
    "max_pages_reached": Outcome.CAP_REACHED,
    # retryable budgets exhausted
    "max_retries_exceeded": Outcome.RETRYABLE_FAILURE,
    "max_empty_pages_exceeded": Outcome.RETRYABLE_FAILURE,
    "parse_failure": Outcome.RETRYABLE_FAILURE,
    "fetch_failure": Outcome.RETRYABLE_FAILURE,
}


def classify_termination(reason: str) -> str:
    """Map a raw termination reason to an `Outcome` category."""
    if not reason:
        return Outcome.PERMANENT_FAILURE
    return _REASON_TO_OUTCOME.get(reason, Outcome.PERMANENT_FAILURE)
