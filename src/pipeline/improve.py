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

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Callable, Dict, Any
import logging
import time

from src.schema.canonical import Observation
from src.pipeline.quality import compute_quality_score

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
    ADJUST_GATE     = "adjust_gate"        # promosikan/relax quality threshold setelah re-collect
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

    def __init__(
        self,
        base_dir,
        collector: Callable[..., "Any"],
        processor: Callable[..., PipelineMetrics],
        planner: Optional[ImprovementPlanner] = None,
        max_iter: int = 3,
        sleep_between: float = 1.0,
    ):
        from pathlib import Path
        self.base_dir = Path(base_dir)
        self.collector = collector
        self.processor = processor
        self.planner = planner or ImprovementPlanner()
        self.max_iter = max_iter
        self.sleep_between = sleep_between
        self.history: List[ImprovementPlan] = []

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
        """Run the self-improvement loop untuk satu video. Selalu berterminasi."""
        m = self.processor([], self.base_dir, video_id=video_id)  # observe existing
        if self.planner.is_stable(m):
            logger.info("[improve] %s already stable (coverage %.0f%%)", video_id, m.coverage * 100)
            return m

        for i in range(1, self.max_iter + 1):
            plan = self.planner.plan(m, iteration=i)
            self.history.append(plan)
            if plan.actions == [Action.NO_CHANGE]:
                logger.info("[improve] %s no-op at iter %d", video_id, i)
                break
            logger.info("[improve] %s iter %d: %s", video_id, i,
                        ", ".join(a.value for a in plan.actions))

            start = time.time()
            overrides = self._apply(plan)
            observations = self._collect(video_id, url, overrides)
            elapsed = time.time() - start
            m_new = self.processor(observations, self.base_dir,
                                   video_id=video_id, iteration=i, elapsed=elapsed)

            self._record_plan(plan, m_new)

            if self.planner.is_stable(m_new):
                logger.info("[improve] %s stabilized at iter %d", video_id, i)
                return m_new
            if not self.planner.is_improving(m, m_new):
                logger.warning("[improve] %s no improvement at iter %d — stop", video_id, i)
                return m_new

            m = m_new
            time.sleep(self.sleep_between)

        logger.warning("[improve] %s budget exhausted (%d iter) — best effort",
                       video_id, self.max_iter)
        return m

    def _record_plan(self, plan: ImprovementPlan, m: PipelineMetrics) -> None:
        """Persist improvement provenance ke manifest (auditable)."""
        import json
        from pathlib import Path
        mfile = self.base_dir / "data" / "manifests" / f"{plan.video_id}.improve.jsonl"
        mfile.parent.mkdir(parents=True, exist_ok=True)
        with mfile.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "iteration": plan.iteration,
                "actions": [a.value for a in plan.actions],
                "rationale": plan.rationale,
                "metrics": m.__dict__ if hasattr(m, "__dict__") else None,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime()),
            }, ensure_ascii=False) + "\n")
