#!/usr/bin/env python3
"""
Loop state + outcome classification for bounded internal loops.

Closes the loop-durability gaps identified in the bounded-autonomous-loop
readiness audit (see PRD §7 / FR-LOOP-001..003):

- **Persisted loop position.** `LoopState` records the next iteration, the
  job identity, the last metrics snapshot and the policy model version in
  force — atomically (tmp+replace, reusing `CheckpointStore`). A crashed or
  interrupted loop resumes at its recorded iteration instead of replanning
  from iteration 1.
- **Idempotent iterations.** Iterations are keyed by `(job_id, iteration)`;
  resume never re-executes an iteration already recorded in the improve
  manifest.
- **Classified endings.** Every loop exit maps deterministically to a
  `LoopOutcome`, which maps additively into the existing run lifecycle
  taxonomy (`src.runtime.termination.RunLifecycle`). No new lifecycle
  meanings are invented; unknown outcomes fail closed to FAILED.

This module is provider-blind and stdlib-only (Constitution §14 / NFR-002).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.policy.models import POLICY_MODEL_VERSION
from src.runtime.checkpoint import CheckpointStore
from src.runtime.termination import RunLifecycle

LOOP_STATE_SCHEMA_VERSION = "1"


class LoopOutcome:
    """Terminal classifications for a bounded loop run.

    Deliberately mirrors the audit's five-state taxonomy. These are LOOP
    outcomes; they are additive to — never a replacement of — the acquisition
    `Outcome` taxonomy in `src/runtime/termination.py`.
    """
    TASK_COMPLETE = "task_complete"        # stability criteria met
    PARTIAL_SUCCESS = "partial_success"    # best-effort stop: no measured improvement
    BUDGET_EXHAUSTED = "budget_exhausted"  # max_iter reached without stability
    BLOCKED = "blocked"                    # externally stopped (policy denial etc.)
    FAILED = "failed"                      # unclassified error — fail-closed default

    ALL = (TASK_COMPLETE, PARTIAL_SUCCESS, BUDGET_EXHAUSTED, BLOCKED, FAILED)

    TERMINAL = ALL  # every outcome is terminal for a loop run


# LoopOutcome → terminal RunLifecycle. Total over LoopOutcome.ALL; unknown
# values map FAILED (fail-closed), mirroring `lifecycle_for_outcome`.
_LOOP_OUTCOME_TO_LIFECYCLE = {
    LoopOutcome.TASK_COMPLETE: RunLifecycle.COMPLETED,
    # best-effort stops are planned/configured outcomes, like CAP_REACHED
    LoopOutcome.PARTIAL_SUCCESS: RunLifecycle.COMPLETED,
    LoopOutcome.BUDGET_EXHAUSTED: RunLifecycle.COMPLETED,
    # externally imposed stops classify TERMINATED, like POLICY_DENIED
    LoopOutcome.BLOCKED: RunLifecycle.TERMINATED,
    LoopOutcome.FAILED: RunLifecycle.FAILED,
}


def lifecycle_for_loop_outcome(outcome: str) -> str:
    """Map a LoopOutcome to its terminal lifecycle state (fail-closed)."""
    if outcome not in _LOOP_OUTCOME_TO_LIFECYCLE:
        return RunLifecycle.FAILED
    return _LOOP_OUTCOME_TO_LIFECYCLE[outcome]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LoopState:
    """Durable position of one bounded loop, keyed by video_id.

    Serialization is fail-closed: missing required keys, unknown schema
    major versions, or wrong-typed fields raise ValueError on load.
    """

    video_id: str
    job_id: str
    next_iteration: int          # 1-based; the next iteration to execute
    applied_overrides: dict      # collector kwargs applied by the last executed iteration
    last_metrics: dict           # PipelineMetrics.__dict__ snapshot of last evaluation
    policy_model_version: str = POLICY_MODEL_VERSION
    outcome: str = ""            # set only when the loop reaches a terminal state
    updated_at: str = ""

    def __post_init__(self):
        if not isinstance(self.video_id, str) or not self.video_id:
            raise ValueError("loop state requires non-empty video_id")
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("loop state requires non-empty job_id")
        if not isinstance(self.next_iteration, int) or self.next_iteration < 1:
            raise ValueError(
                f"next_iteration must be a positive int, got {self.next_iteration!r}"
            )
        if not isinstance(self.applied_overrides, dict):
            raise ValueError("applied_overrides must be a dict")
        if not isinstance(self.last_metrics, dict):
            raise ValueError("last_metrics must be a dict")
        if not self.updated_at:
            self.updated_at = _utc_now()

    def to_dict(self) -> dict:
        return {
            "schema_version": LOOP_STATE_SCHEMA_VERSION,
            "video_id": self.video_id,
            "job_id": self.job_id,
            "next_iteration": self.next_iteration,
            "applied_overrides": dict(self.applied_overrides),
            "last_metrics": dict(self.last_metrics),
            "policy_model_version": self.policy_model_version,
            "outcome": self.outcome,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LoopState":
        if not isinstance(d, dict):
            raise ValueError(f"loop state payload must be a dict, got {type(d)!r}")
        version = d.get("schema_version", "")
        # additive evolution only: same-major versions load; future majors reject.
        if version.split(".")[0] != LOOP_STATE_SCHEMA_VERSION.split(".")[0]:
            raise ValueError(
                f"unsupported loop-state schema_version: {version!r} "
                f"(this reader understands {LOOP_STATE_SCHEMA_VERSION})"
            )
        try:
            return cls(
                video_id=d["video_id"],
                job_id=d["job_id"],
                next_iteration=d["next_iteration"],
                applied_overrides=dict(d.get("applied_overrides", {})),
                last_metrics=dict(d.get("last_metrics", {})),
                policy_model_version=d.get("policy_model_version", POLICY_MODEL_VERSION),
                outcome=d.get("outcome", ""),
                updated_at=d.get("updated_at", ""),
            )
        except KeyError as e:
            raise ValueError(f"loop state missing required key: {e}") from e


class LoopStateStore:
    """Atomic persistence for one loop's `LoopState`.

    Reuses `CheckpointStore` (tmp write + atomic replace). A corrupt state
    file raises `CheckpointCorrupt` — fail-closed per Constitution §3: resume
    never guesses, and discarding the file is an explicit human decision.
    """

    def __init__(self, path):
        self._store = CheckpointStore(path)

    @property
    def path(self):
        return self._store.path

    def exists(self) -> bool:
        return self._store.exists()

    def save(self, state: LoopState) -> None:
        if not isinstance(state, LoopState):
            raise ValueError(f"expected LoopState, got {type(state)!r}")
        self._store.save(state.to_dict())

    def load(self):
        """Return the stored `LoopState`, or None if no loop was started."""
        d = self._store.load()  # CheckpointCorrupt propagates — never silent
        if d is None:
            return None
        return LoopState.from_dict(d)
