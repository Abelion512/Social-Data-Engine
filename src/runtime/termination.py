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
    # Additive policy-gate outcomes (PolicyEvaluator v0). Legacy outcomes and
    # reason strings are unchanged; these are NEW classifications only.
    POLICY_DENIED = "policy_denied"            # policy gate refused the run (fail closed)
    APPROVAL_REQUIRED = "approval_required"    # policy requires approval before executing

    ALL = (SUCCESS, RETRYABLE_FAILURE, PERMANENT_FAILURE, AUTH_BLOCKED,
           PAGINATION_STALL, CAP_REACHED,
           POLICY_DENIED, APPROVAL_REQUIRED)


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
    # policy gate (added by PolicyEvaluator v0 — never renamed)
    "policy_denied": Outcome.POLICY_DENIED,
    "approval_required": Outcome.APPROVAL_REQUIRED,
}


def classify_termination(reason: str) -> str:
    """Map a raw termination reason to an `Outcome` category."""
    if not reason:
        return Outcome.PERMANENT_FAILURE
    return _REASON_TO_OUTCOME.get(reason, Outcome.PERMANENT_FAILURE)


class RunLifecycle:
    """Actor-run lifecycle states (harness-owned vocabulary).

    Only states with an actual consumer exist:
      - CREATED   : input validated + context built, runtime not yet invoked
                    (transient, in-memory — nothing persists in this state)
      - STARTED   : the runtime began executing (persisted in checkpoints so a
                    crashed run is visibly 'started', never silently ambiguous)
      - COMPLETED : terminal — planned stop (success or configured cap reached)
      - FAILED    : terminal — error-classified stop (retryable/permanent failure,
                    incl. explicit recovery failures like `checkpoint_corrupt`)
      - TERMINATED: terminal — externally stopped (auth block / pagination stall);
                    the actor did not fail and did not complete its work

    There is deliberately no SUSPENDED/PENDING/APPROVAL state: suspension via
    REQUIRE_APPROVAL is PLANNED (docs/architecture/POLICY-ARCHITECTURE.md) and
    gets added when something consumes it.
    """
    CREATED = "created"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"

    ALL = (CREATED, STARTED, COMPLETED, FAILED, TERMINATED)


# Outcome → terminal lifecycle state. Total over Outcome.ALL — every outcome
# classifies; unknown outcomes fail visibly as FAILED (fail-closed).
_OUTCOME_TO_LIFECYCLE = {
    Outcome.SUCCESS: RunLifecycle.COMPLETED,
    Outcome.CAP_REACHED: RunLifecycle.COMPLETED,       # cap = configured intent
    Outcome.AUTH_BLOCKED: RunLifecycle.TERMINATED,
    Outcome.PAGINATION_STALL: RunLifecycle.TERMINATED,
    Outcome.RETRYABLE_FAILURE: RunLifecycle.FAILED,
    Outcome.PERMANENT_FAILURE: RunLifecycle.FAILED,
    # Policy-gated stops are externally imposed (the actor did not fail), so
    # they classify TERMINATED like auth/stall. APPROVAL_REQUIRED maps here
    # until a real SUSPENDED state exists (Constitution §10 PLANNED) — there
    # is deliberately no fake suspension semantics in v0.
    Outcome.POLICY_DENIED: RunLifecycle.TERMINATED,
    Outcome.APPROVAL_REQUIRED: RunLifecycle.TERMINATED,
}


def lifecycle_for_outcome(outcome: str) -> str:
    """Map an `Outcome` category to its terminal lifecycle state.

    Unknown/None outcomes map to FAILED (fail-closed), mirroring
    `classify_termination`'s treatment of unknown reasons.
    """
    return _OUTCOME_TO_LIFECYCLE.get(outcome, RunLifecycle.FAILED)
