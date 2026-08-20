#!/usr/bin/env python3
"""
Unit tests untuk Auto/Recursive Self-Improvement Architecture.

Fokus: ImprovementPlanner (deterministic rule engine) + SelfHealingPipeline
loop logic. Semua test **mock** — tidak butuh browser, jaringan, atau LLM.

Jalankan:  python tests/test_self_improvement.py
atau:       python -m pytest tests/test_self_improvement.py -v
"""
import sys
import time
from pathlib import Path

# Setup path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.improve import (
    PipelineMetrics,
    ImprovementPlan,
    Action,
    ImprovementPlanner,
    SelfHealingPipeline,
)


def _m(video_id="v1", coverage=1.0, avg_quality=0.5, dup_rate=0.05,
       partial=False, stall_reason=""):
    """Helper: buat PipelineMetrics dengan nilai default stabil."""
    return PipelineMetrics(
        video_id=video_id,
        reported=100,
        captured=int(coverage * 100),
        coverage=coverage,
        avg_quality=avg_quality,
        dup_rate=dup_rate,
        partial=partial,
        stall_reason=stall_reason,
        collected_at="2026-08-20T00:00:00Z",
    )


# ── Planner: decision matrix (pure, deterministic) ────────────────────────────

def test_planner_stable_metrics_no_action():
    """Stabil: coverage ≥ 95%, quality ≥ 0.35, tidak partial → NO_ACTION."""
    planner = ImprovementPlanner()
    p = planner.plan(_m(coverage=0.97, avg_quality=0.45), iteration=0)
    assert Action.NO_CHANGE in p.actions
    assert Action.RETRY_BROWSER not in p.actions
    assert Action.ADJUST_GATE not in p.actions
    print("PASS: test_planner_stable_metrics_no_action")


def test_planner_low_coverage_partial():
    """Coverage < 95% (partial) → RECOVER + EXPAND + SPLIT."""
    planner = ImprovementPlanner()
    p = planner.plan(_m(coverage=0.40, partial=True), iteration=1)
    actions = p.actions
    assert Action.RECOVER_PARTIAL in actions
    assert Action.EXPAND_REPLIES in actions
    assert Action.SPLIT_BATCH in actions
    assert Action.RETRY_BROWSER not in actions  # tidak crash
    assert "coverage 40%" in p.rationale
    print("PASS: test_planner_low_coverage_partial")


def test_planner_crash_stall():
    """Stall reason crash/EPIPE → RETRY_BROWSER + SPLIT (bukan recover)."""
    planner = ImprovementPlanner()
    p = planner.plan(_m(coverage=0.50, partial=True, stall_reason="EPIPE on batch 2"), iteration=1)
    assert Action.RETRY_BROWSER in p.actions
    assert Action.SPLIT_BATCH in p.actions
    assert Action.RECOVER_PARTIAL not in p.actions
    print("PASS: test_planner_crash_stall")


def test_planner_low_quality_triggers_gate_adjust():
    """avg_quality < target → ADJUST_GATE."""
    planner = ImprovementPlanner()
    p = planner.plan(_m(coverage=0.98, avg_quality=0.10), iteration=1)
    assert Action.ADJUST_GATE in p.actions
    print("PASS: test_planner_low_quality_triggers_gate_adjust")


def test_planner_high_dup_rate_only_remarks():
    """Dup rate tinggi tapi coverage stabil → tidak ada aksi (data char, bukan bug)."""
    planner = ImprovementPlanner()
    p = planner.plan(_m(coverage=0.99, avg_quality=0.5, dup_rate=0.60), iteration=0)
    assert Action.NO_CHANGE in p.actions  # duplikat bukan bug collector
    assert "dup_rate" in p.rationale
    print("PASS: test_planner_high_dup_rate_only_remarks")


def test_is_stable():
    planner = ImprovementPlanner()
    assert planner.is_stable(_m(coverage=1.0, avg_quality=0.5)) is True
    assert planner.is_stable(_m(coverage=0.5, partial=True)) is False
    assert planner.is_stable(_m(coverage=0.99, avg_quality=0.1)) is False
    print("PASS: test_is_stable")


def test_is_improving():
    planner = ImprovementPlanner()
    prev = _m(coverage=0.40)
    assert planner.is_improving(prev, _m(coverage=0.50)) is True   # +10pp
    assert planner.is_improving(prev, _m(coverage=0.44)) is False  # +4pp < delta 5pp
    # sudah stabil → not improving (should stop)
    assert planner.is_improving(None, _m(coverage=0.99, avg_quality=0.5)) is False
    # belum stabil, belum ada baseline → lanjutkan
    assert planner.is_improving(None, _m(coverage=0.50, partial=True)) is True
    print("PASS: test_is_improving")


# ── SelfHealingPipeline: loop logic (mock collector/processor) ───────────────

def test_self_healing_already_stable_skips_loop():
    """Jika metrics sudah stabil, loop tidak dijalankan (no collect call)."""
    calls = {"collect": 0}

    def mock_processor(existing, base_dir, **kw):
        # return stable metrics straight away
        return _m(video_id="v1", coverage=1.0, avg_quality=0.5)

    def mock_collector(video_id, url, **kw):
        calls["collect"] += 1
        return []

    runner = SelfHealingPipeline(
        base_dir=ROOT, collector=mock_collector, processor=mock_processor,
        max_iter=3, sleep_between=0,
    )
    result = runner.run("v1", "https://www.tiktok.com/@x/video/123")
    assert calls["collect"] == 0, "should skip collect when already stable"
    assert result.coverage == 1.0
    assert len(runner.history) == 0
    print("PASS: test_self_healing_already_stable_skips_loop")


def test_self_healing_loop_then_stabilize():
    """Iterasi ke-1 gagal (low coverage) → collect berulang → stabil iterasi ke-2."""
    calls = {"collect": 0}
    states = [
        # iter 0 (observe existing): unstable
        _m(video_id="v1", coverage=0.40, partial=True, stall_reason="low"),
        # iter 1 after collect: still improving but not stable
        _m(video_id="v1", coverage=0.60, partial=True),
        # iter 2 after collect: stable
        _m(video_id="v1", coverage=0.96, avg_quality=0.5),
    ]
    idx = {"i": 0}

    def mock_processor(existing, base_dir, **kw):
        i = idx["i"]; idx["i"] += 1
        return states[min(i, len(states) - 1)]

    def mock_collector(video_id, url, **kw):
        calls["collect"] += 1
        return []

    runner = SelfHealingPipeline(
        base_dir=ROOT, collector=mock_collector, processor=mock_processor,
        max_iter=5, sleep_between=0,
    )
    result = runner.run("v1", "https://www.tiktok.com/@x/video/123")
    assert calls["collect"] == 2, f"expected 2 collect calls, got {calls['collect']}"
    assert result.coverage == 0.96
    assert len(runner.history) == 2
    # plan actions should include REMEDIAL actions, not NO_CHANGE
    assert runner.history[0].actions != [Action.NO_CHANGE]
    print("PASS: test_self_healing_loop_then_stabilize")


def test_self_healing_budget_exhausted():
    """Jika tidak pernah stabil sampai max_iter → berhenti, kembalikan best-effort."""
    calls = {"collect": 0}
    # Setiap iterasi membaik tapi belum mencapai target stabil (coverage < 95%)
    states = [
        _m(video_id="v1", coverage=0.40, partial=True),   # observe (existing)
        _m(video_id="v1", coverage=0.60, partial=True),   # iter 1
        _m(video_id="v1", coverage=0.70, partial=True),   # iter 2 — budget habis
    ]
    idx = {"i": 0}

    def mock_processor(existing, base_dir, **kw):
        i = idx["i"]; idx["i"] += 1
        return states[min(i, len(states) - 1)]

    def mock_collector(video_id, url, **kw):
        calls["collect"] += 1
        return []

    runner = SelfHealingPipeline(
        base_dir=ROOT, collector=mock_collector, processor=mock_processor,
        max_iter=2, sleep_between=0,
    )
    result = runner.run("v1", "https://www.tiktok.com/@x/video/123")
    assert calls["collect"] == 2, f"expected 2, got {calls['collect']}"
    assert result.coverage == 0.70
    assert len(runner.history) == 2
    # manifest file should be written
    mfile = ROOT / "data" / "manifests" / "v1.improve.jsonl"
    assert mfile.exists(), "improvement manifest not written"
    mfile.unlink()
    print("PASS: test_self_healing_budget_exhausted")


# ── Action enum sanity ────────────────────────────────────────────────────────

def test_action_enum_values():
    assert Action.RETRY_BROWSER.value == "retry_browser"
    assert Action.SPLIT_BATCH.value == "split_batch"
    assert Action.SWITCH_PROVIDER.value == "switch_provider"
    assert Action.REQUEUE.value == "requeue"
    print("PASS: test_action_enum_values")


def main():
    test_planner_stable_metrics_no_action()
    test_planner_low_coverage_partial()
    test_planner_crash_stall()
    test_planner_low_quality_triggers_gate_adjust()
    test_planner_high_dup_rate_only_remarks()
    test_is_stable()
    test_is_improving()
    test_self_healing_already_stable_skips_loop()
    test_self_healing_loop_then_stabilize()
    test_self_healing_budget_exhausted()
    test_action_enum_values()
    print("\nAll self-improvement tests passed ✅")


if __name__ == "__main__":
    main()
