#!/usr/bin/env python3
"""
Auto / Recursive Self-Improvement Architecture
================================================================
Architectural feedback loop — **bukan model improvement**.

The system observes its own collection / pipeline results, detects
regressions or stalls, plans deterministic remediation actions, applies
them, re-runs, and re-evaluates — recursing until the result is stable or
the iteration budget is exhausted.

   ┌─────────────┐  metrics   ┌──────────┐  plan   ┌──────────┐
   │  Observer   │ ────────▶ │ Planner  │ ──────▶ │ Actions  │
   │ (Pipeline   │            │ (rules)  │         │ (retry,  │
   │  Metrics)   │  ◀──────  │          │  ◀──── │  split,  │
   │             │  re-measure│          │         │  switch) │
   └─────────────┘            └────┬─────┘         └─────┬────┘
                                   │ execute              │
                                   ▼                      │
   ┌──────────┐  ┌────────────────────────────────────────────┐
   │ Evaluator│  │  Executor (acts on the collector config)   │
   │(stable?) │  │  — re-invoke collect() with new params     │
   └────┬─────┘  └────────────────────────────────────────────┘
        │
      ya│   selesai (atau budget habis)
        ▼

Poin desain kunci:
* **Planner bersifat deterministik** (rule engine) — tidak butuh LLM apapun.
* **Executor** yang menjalankan aksi (browser baru, batch kecil, provider ganti)
  dipisahkan dari logika plan, sehingga bisa diganti mock untuk test.
* Loop terbatas `max_iter` agar selalu berterminasi; setiap iterasi mencatat
  `iteration` + `rationale` ke manifest agar proses improvement **tertular
  (auditable/provenance-aware)**.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Callable, Dict, Any
import logging
import time
import uuid

from src.runtime.checkpoint import CheckpointCorrupt  # re-exported for callers
from src.runtime.context import require_slug_identifier
from src.runtime.loop_state import (
    LOOP_STATE_SCHEMA_VERSION,
    LoopOutcome,
    LoopState,
    LoopStateStore,
    lifecycle_for_loop_outcome,
)
from src.schema.canonical import Observation

logger = logging.getLogger(__name__)


# ── Metrics ───────────────────────────────────────────────────────────────────

@dataclass
class PipelineMetrics:
    """Snapshot observability hasil satu video — input ke ImprovementPlanner."""
    video_id: str
    reported: int                # total komentar yang TikTok laporkan
    captured: int                # berapa yang berhasil di-collect
    coverage: float              # captured / reported  (0..1)
    avg_quality: float           # rata-rata curated_score komentar
    dup_rate: float              # fraksi duplikat yang dibuang (0..1)
    partial: bool                # True bila data tidak lengkap / stall
    stall_reason: str = ""       # mis. "EPIPE", "crash", "login_required"
    collected_at: str = ""       # timestamp observasi


@dataclass
class ImprovementPlan:
    """Rencana aksi yang dihasilkan Planner — bersifat data, deterministic."""
    video_id: str
    iteration: int
    actions: List["Action"]
    rationale: str
    confidence_boost: bool = False  # flag: hasil collect sudah cukup untuk reprocess identity


class Action(str, Enum):
    """Aksi remediatif yang dapat diambil Executor."""
    RETRY_BROWSER   = "retry_browser"      # sesi browser baru (Camoufox EPIPE/crash)
    SPLIT_BATCH     = "split_batch"        # kecilkan batch / chunk scroll
    SWITCH_PROVIDER = "switch_provider"    # ganti driver (camoufox ↔ nodriver ↔ playwright)
    EXPAND_REPLIES  = "expand_replies"     # naikkan kedalaman recursive reply expand
    RECOVER_PARTIAL = "recover_partial"    # re-collect saja reply yang missing
    ADJUST_GATE     = "adjust_gate"        # TIGHTEN-ONLY quality threshold raise (SDD invariant 3:
                                           # loosening an acceptance criterion is intent mutation
                                           # and requires external configuration — never autonomous)
    REQUEUE         = "requeue"            # beri upa kesempatan lain, atau abandone gracefully
    NO_CHANGE       = "no_change"          # tidak perlu aksi


# ── Planner (pure / deterministic rule engine — no model) ─────────────────────

class ImprovementPlanner:
    """
    Rule-based planner over :class:`PipelineMetrics`.

    Tidak ada dependency pada jaringan / browser / LLM — sepenuhnya
    deterministik sehingga bisa diuji.
    """

    # Stabilisasi kriteria (MVP v1)
    COVERAGE_TARGET = 0.95          # ≥95 % coverage dianggap complete
    DUP_TOLERANCE = 0.20            # dup_rate > 20 % perlu perhatian
    QUALITY_TARGET = 0.35           # avg_quality (curated_score) minimum
    MIN_IMPROVEMENT_DELTA = 0.05    # naik sekurang-kurangnya 5 pp tiap iterasi

    def plan(self, m: PipelineMetrics, iteration: int = 0) -> ImprovementPlan:
        actions: List[Action] = []
        rationale_parts: List[str] = []

        # 1. Coverage / partial / stall
        if m.partial or m.coverage < self.COVERAGE_TARGET:
            sr = m.stall_reason.lower()
            if "epipe" in sr or "crash" in sr or "browser" in sr:
                actions.append(Action.RETRY_BROWSER)
                actions.append(Action.SPLIT_BATCH)
                rationale_parts.append(
                    "stall/browser-crash terdeteksi → fresh browser + batch kecil"
                )
            else:
                actions.append(Action.RECOVER_PARTIAL)
                actions.append(Action.EXPAND_REPLIES)
                actions.append(Action.SPLIT_BATCH)
                rationale_parts.append(
                    f"coverage {m.coverage:.0%} < target {self.COVERAGE_TARGET:.0%}"
                    " → re-collect partial + reply expand"
                )
        else:
            rationale_parts.append(f"coverage {m.coverage:.0%} sudah memadai")

        # 2. Dup rate (data characteristic, not a collector bug — log only)
        if m.dup_rate > self.DUP_TOLERANCE:
            rationale_parts.append(
                f"dup_rate {m.dup_rate:.0%} tinggi — perhatikan; bukan bug collector"
            )

        # 3. Quality gate
        if m.avg_quality < self.QUALITY_TARGET:
            actions.append(Action.ADJUST_GATE)
            rationale_parts.append(
                f"avg_quality {m.avg_quality:.2f} < {self.QUALITY_TARGET}"
                " → promosikan threshold + re-normalize"
            )

        # 4. Jika tidak ada aksi signifikan
        if not actions:
            actions = [Action.NO_CHANGE]
            rationale_parts.append("tidak ada regresi terdeteksi")

        return ImprovementPlan(
            video_id=m.video_id,
            iteration=iteration,
            actions=actions,
            rationale="; ".join(rationale_parts),
        )

    def is_stable(self, m: PipelineMetrics) -> bool:
        """Apakah metric sudah memenuhi kriteria stabil MVP v1?"""
        return (
            not m.partial
            and m.coverage >= self.COVERAGE_TARGET
            and m.avg_quality >= self.QUALITY_TARGET
        )

    def is_improving(self, prev: Optional[PipelineMetrics],
                     cur: PipelineMetrics) -> bool:
        """True bila perlu/usaha masih berguna (belum stabil & ada ruang naik)."""
        if self.is_stable(cur):
            return False                       # sudah memuaskan — stop
        if prev is None:
            return True                        # belum ada baseline — lanjutkan
        return (cur.coverage - prev.coverage) >= self.MIN_IMPROVEMENT_DELTA


# ── Executor (pluggable — default re-runs collect+process) ────────────────────

class SelfHealingPipeline:
    """
    Orchestrator rekursif: observe → plan → act → re-run → evaluate.

    Parameters
    ----------
    base_dir : Path
        Root folder berisi ``data/`` (raw/normalized/...).
    collector : Callable
        ``collector(video_id, url, **overrides) -> List[RawComment]``
        Harus menerima override params (provider, batch, reply_depth).
    processor : Callable
        ``processor(raw_list, base_dir) -> PipelineMetrics``
        Normalize→dedup→quality, kemudian kembalikan metrik.
    max_iter : int
        Batas rekursi (safety — selalu berterminasi).
    """

    MAX_ITER_CAP = 10  # sane ceiling: callers may lower, never exceed (FR-LOOP-001)

    def __init__(
        self,
        base_dir,
        collector: Callable[..., "Any"],
        processor: Callable[..., PipelineMetrics],
        planner: Optional[ImprovementPlanner] = None,
        max_iter: int = 3,
        sleep_between: float = 1.0,
        state_dir=None,
    ):
        from pathlib import Path
        if not isinstance(max_iter, int) or max_iter < 1:
            raise ValueError(f"max_iter must be a positive int, got {max_iter!r}")
        if max_iter > self.MAX_ITER_CAP:
            raise ValueError(
                f"max_iter={max_iter} exceeds the sane cap {self.MAX_ITER_CAP} "
                "(FR-LOOP-001); lower the value"
            )
        self.base_dir = Path(base_dir)
        self.collector = collector
        self.processor = processor
        self.planner = planner or ImprovementPlanner()
        self.max_iter = max_iter
        self.sleep_between = sleep_between
        self.state_dir = Path(state_dir) if state_dir else self.base_dir / "state" / "loops"
        self.history: List[ImprovementPlan] = []
        # populated by run(): machine-readable terminal classification
        self.last_outcome: Optional[str] = None
        self.last_job_id: Optional[str] = None

    # ── hooks (dapat di-override subclass / mock) ────────────────────────────

    def _apply(self, plan: ImprovementPlan) -> Dict[str, Any]:
        """Terjemahkan Action list → collector kwargs overrides."""
        overrides: Dict[str, Any] = {}
        provider_map = {
            Action.SWITCH_PROVIDER: "camoufox",   # sensible default swap
        }
        for a in plan.actions:
            if a == Action.RETRY_BROWSER:
                overrides["fresh_browser"] = True
            elif a == Action.SPLIT_BATCH:
                overrides["scrolls"] = max(1, overrides.get("scrolls", 60) // 2)
            elif a == Action.SWITCH_PROVIDER:
                overrides["provider"] = provider_map[Action.SWITCH_PROVIDER]
            elif a == Action.EXPAND_REPLIES:
                overrides["reply_depth"] = min(
                    overrides.get("reply_depth", 3) + 2, 7
                )
            elif a == Action.RECOVER_PARTIAL:
                overrides["retry_partial"] = True
            elif a == Action.ADJUST_GATE:
                overrides["adjust_gate"] = True
        return overrides

    def _collect(self, video_id, url, overrides) -> List["Observation"]:
        kwargs = dict(overrides)
        return self.collector(video_id, url, **kwargs)

    # ── main loop ───────────────────────────────────────────────────────────

    def run(self, video_id: str, url: str) -> PipelineMetrics:
        """Run the self-improvement loop untuk satu video. Selalu berterminasi.

        Durable + resumable: the loop position persists in
        ``<base_dir>/state/loops/<video_id>.json`` (atomic tmp+replace). A
        re-invocation with existing state RESUMES that loop under the same
        ``job_id`` and skips iterations already recorded in the improve
        manifest — a crash between dataset write and manifest append can
        never cause the same iteration to execute twice. A corrupt state
        file raises ``CheckpointCorrupt`` (fail-closed; never silently
        restarted — recovery is a human decision, Constitution §3).

        A persisted state that already carries a terminal ``outcome`` is
        honored verbatim: rerunning returns the recorded result without
        executing any further iteration, collection, or state rewrite —
        ``job_id``, ``outcome`` and last metrics survive untouched.

        The terminal classification lands on ``self.last_outcome`` as one of
        ``LoopOutcome.ALL`` and maps additively into the run lifecycle via
        ``lifecycle_for_loop_outcome`` (FR-RUN-001 discipline).

        S-G2 (threat model §3.D item 1): ``video_id`` is validated against the
        strict slug charset BEFORE any state file or manifest path is even
        NAMED — a traversal identifier raises ``ValueError`` before any
        filesystem mutation can occur.
        """
        require_slug_identifier(video_id, "video_id")
        store = LoopStateStore(self.state_dir / f"{video_id}.json")
        saved = store.load()  # CheckpointCorrupt propagates — fail closed
        if saved is not None and saved.outcome:
            # Terminal loop state on disk (LoopOutcome.TERMINAL == ALL):
            # honor it — no further iteration, no collection, no state
            # rewrite. Unknown outcome markers fail closed to FAILED while
            # staying terminal (never guess into more work, Constitution §3).
            outcome = saved.outcome if saved.outcome in LoopOutcome.ALL \
                else LoopOutcome.FAILED
            logger.info("[improve] %s already terminal (%s → %s) — "
                        "no further iterations", video_id, outcome,
                        lifecycle_for_loop_outcome(outcome))
            self.last_outcome = outcome
            self.last_job_id = saved.job_id
            return self._metrics_from_state(saved)
        if saved is not None:
            job_id = saved.job_id
            start_iteration = saved.next_iteration
            logger.info("[improve] %s resuming job %s at iter %d",
                        video_id, job_id, start_iteration)
        else:
            job_id = uuid.uuid4().hex[:12]
            start_iteration = 1

        m = self.processor([], self.base_dir, video_id=video_id)  # observe existing
        if self.planner.is_stable(m):
            logger.info("[improve] %s already stable (coverage %.0f%%)", video_id, m.coverage * 100)
            return self._finish(video_id, job_id, start_iteration, {}, m,
                                LoopOutcome.TASK_COMPLETE)

        recorded = self._recorded_iterations(video_id, job_id)
        overrides: Dict[str, Any] = {}

        try:
            for i in range(start_iteration, self.max_iter + 1):
                if i in recorded:
                    logger.info("[improve] %s iter %d already recorded — skip", video_id, i)
                    continue

                plan = self.planner.plan(m, iteration=i)
                self.history.append(plan)
                if plan.actions == [Action.NO_CHANGE]:
                    logger.info("[improve] %s no-op at iter %d", video_id, i)
                    return self._finish(video_id, job_id, i, overrides, m,
                                        LoopOutcome.PARTIAL_SUCCESS)
                logger.info("[improve] %s iter %d: %s", video_id, i,
                            ", ".join(a.value for a in plan.actions))

                start = time.time()
                overrides = self._apply(plan)
                observations = self._collect(video_id, url, overrides)
                elapsed = time.time() - start
                m_new = self.processor(observations, self.base_dir,
                                       video_id=video_id, iteration=i, elapsed=elapsed)

                # Evidence first (manifest), then durable position (state).
                # Crash before the manifest line: iteration re-executes, but
                # dataset writes are id-dedup'd so no duplication occurs.
                # Crash after it: resume skips this iteration via `recorded`.
                self._record_plan(plan, m_new, job_id=job_id)
                self._save_state(video_id, job_id, i + 1, overrides, m_new)

                if self.planner.is_stable(m_new):
                    logger.info("[improve] %s stabilized at iter %d", video_id, i)
                    return self._finish(video_id, job_id, i + 1, overrides, m_new,
                                        LoopOutcome.TASK_COMPLETE)
                if not self.planner.is_improving(m, m_new):
                    logger.warning("[improve] %s no improvement at iter %d — stop", video_id, i)
                    return self._finish(video_id, job_id, i + 1, overrides, m_new,
                                        LoopOutcome.PARTIAL_SUCCESS)

                m = m_new
                time.sleep(self.sleep_between)
        except Exception:
            # Fail loudly but leave an inspectable terminal trace (fail-closed,
            # Constitution §3: failure must still checkpoint).
            self.last_outcome = LoopOutcome.FAILED
            self.last_job_id = job_id
            try:
                self._save_state(video_id, job_id, start_iteration, overrides, m,
                                 outcome=LoopOutcome.FAILED)
            except Exception:
                pass
            raise

        logger.warning("[improve] %s budget exhausted (%d iter) — best effort",
                       video_id, self.max_iter)
        return self._finish(video_id, job_id, self.max_iter + 1, overrides, m,
                            LoopOutcome.BUDGET_EXHAUSTED)

    def _record_plan(self, plan: ImprovementPlan, m: PipelineMetrics,
                     job_id: str = "") -> None:
        """Persist improvement provenance ke manifest (auditable).

        Additive fields `job_id` and `policy_model_version` make iterations
        idempotency-keyable and version-attributable (audit P1-1/P1-3).
        Legacy readers ignore unknown keys.
        """
        import json
        from src.policy.models import POLICY_MODEL_VERSION
        mfile = self.manifest_path(plan.video_id)
        mfile.parent.mkdir(parents=True, exist_ok=True)
        with mfile.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "iteration": plan.iteration,
                "job_id": job_id,
                "actions": [a.value for a in plan.actions],
                "rationale": plan.rationale,
                "metrics": m.__dict__ if hasattr(m, "__dict__") else None,
                "policy_model_version": POLICY_MODEL_VERSION,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime()),
            }, ensure_ascii=False) + "\n")

    # ── durable loop position (LoopState) ──────────────────────────────────

    def manifest_path(self, video_id: str):
        # S-G2: manifest path builders refuse hostile identifiers outright.
        require_slug_identifier(video_id, "video_id")
        return self.base_dir / "data" / "manifests" / f"{video_id}.improve.jsonl"

    def _recorded_iterations(self, video_id: str, job_id: str) -> set:
        """Iteration numbers already executed for this loop.

        Matches lines with the same job_id, plus legacy lines without a
        job_id field (pre-dating idempotency keys — conservatively treated
        as belonging to this video's loop).
        """
        import json
        recorded: set = set()
        mfile = self.manifest_path(video_id)
        if not mfile.exists():
            return recorded
        with mfile.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn tail line from a crash mid-append: ignorable
                if d.get("job_id", job_id) != job_id:
                    continue
                it = d.get("iteration")
                if isinstance(it, int):
                    recorded.add(it)
        return recorded

    def _save_state(self, video_id: str, job_id: str, next_iteration: int,
                    overrides: Dict[str, Any], metrics: PipelineMetrics,
                    outcome: str = "") -> None:
        require_slug_identifier(video_id, "video_id")
        store = LoopStateStore(self.state_dir / f"{video_id}.json")
        store.save(LoopState(
            video_id=video_id,
            job_id=job_id,
            next_iteration=next_iteration,
            applied_overrides=dict(overrides),
            last_metrics=vars(metrics) if hasattr(metrics, "__dict__") else {},
            outcome=outcome,
        ))

    def _metrics_from_state(self, saved: LoopState) -> PipelineMetrics:
        """Rebuild the last observed metrics from durable loop state."""
        snap = dict(saved.last_metrics)
        try:
            return PipelineMetrics(
                video_id=saved.video_id,
                reported=int(snap.get("reported", 0)),
                captured=int(snap.get("captured", 0)),
                coverage=float(snap.get("coverage", 0.0)),
                avg_quality=float(snap.get("avg_quality", 0.0)),
                dup_rate=float(snap.get("dup_rate", 0.0)),
                partial=bool(snap.get("partial", True)),
                stall_reason=str(snap.get("stall_reason", "")),
                collected_at=str(snap.get("collected_at", "")),
            )
        except (TypeError, ValueError):
            # Degraded snapshot: surface loudly instead of guessing into a
            # fabricated "good" result (fail-closed, Constitution §3).
            raise ValueError(
                f"loop state for {saved.video_id} holds an unusable "
                "last_metrics snapshot"
            ) from None

    def _finish(self, video_id: str, job_id: str, next_iteration: int,
                overrides: Dict[str, Any], m: PipelineMetrics,
                outcome: str) -> PipelineMetrics:
        """Record the terminal classification durably, then surface it."""
        self._save_state(video_id, job_id, next_iteration, overrides, m,
                         outcome=outcome)
        self.last_outcome = outcome
        self.last_job_id = job_id
        logger.info("[improve] %s finished: %s (%s)", video_id, outcome,
                    lifecycle_for_loop_outcome(outcome))
        return m
