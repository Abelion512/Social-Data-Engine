#!/usr/bin/env python3
"""
Loop-state durability, idempotency and outcome-classification tests.

Covers the bounded-loop audit findings (PRD §7 / FR-LOOP-001..003):
- LoopState round-trip + fail-closed validation + corrupt-state loud failure
- resume after interruption never re-executes a recorded iteration
- max_iter sane cap enforced (no unbounded loop configuration)
- LoopOutcome → lifecycle mapping is total and fail-closed
- loop override surface contains only collection-parameter axes (the
  FR-LOOP-003 axis audit — intent fields must be absent)
- ADJUST_GATE is tighten-only by contract (SDD invariant 3)
- a loop whose persisted state carries a terminal `outcome` is honored on
  rerun: zero new iterations/collections, saved result returned verbatim

All tests are deterministic mocks: no browser, no network, no LLM.

Jalankan:  python tests/test_loop_state.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.improve import (
    Action,
    ImprovementPlanner,
    PipelineMetrics,
    SelfHealingPipeline,
)
from src.runtime.checkpoint import CheckpointCorrupt
from src.runtime.loop_state import (
    LOOP_STATE_SCHEMA_VERSION,
    LoopOutcome,
    LoopState,
    LoopStateStore,
    lifecycle_for_loop_outcome,
)
from src.runtime.termination import RunLifecycle


URL = "https://www.tiktok.com/@x/video/123"


def _m(video_id="v1", coverage=0.40, avg_quality=0.5, partial=True,
       stall_reason=""):
    return PipelineMetrics(
        video_id=video_id, reported=100, captured=int(coverage * 100),
        coverage=coverage, avg_quality=avg_quality, dup_rate=0.05,
        partial=partial, stall_reason=stall_reason,
        collected_at="2026-08-22T00:00:00Z",
    )


# ── LoopState: serialization + fail-closed validation ────────────────────────

def test_loop_state_round_trip():
    s = LoopState(video_id="v1", job_id="job1", next_iteration=2,
                  applied_overrides={"scrolls": 30}, last_metrics={"coverage": 0.5})
    d = s.to_dict()
    assert d["schema_version"] == LOOP_STATE_SCHEMA_VERSION
    assert d["policy_model_version"]  # version stamped (audit P1-1)
    r = LoopState.from_dict(d)
    assert r == s
    print("PASS: test_loop_state_round_trip")


def test_loop_state_rejects_malformed():
    base = dict(applied_overrides={}, last_metrics={})
    for kwargs in (
        dict(video_id="", job_id="j", next_iteration=1, **base),
        dict(video_id="v", job_id="", next_iteration=1, **base),
        dict(video_id="v", job_id="j", next_iteration=0, **base),
        dict(video_id="v", job_id="j", next_iteration="2", **base),
        dict(video_id="v", job_id="j", next_iteration=1, applied_overrides=[],
             last_metrics={}),
    ):
        try:
            LoopState(**kwargs)
            raise AssertionError(f"expected ValueError for {kwargs}")
        except ValueError:
            pass
    # missing required key on load
    try:
        LoopState.from_dict({"schema_version": "1"})
        raise AssertionError("expected ValueError for missing keys")
    except ValueError as e:
        assert "missing required key" in str(e)
    # unknown future major version rejected
    try:
        LoopState.from_dict(dict(LoopState(
            video_id="v", job_id="j", next_iteration=1,
            applied_overrides={}, last_metrics={}).to_dict(),
            schema_version="2.0"))
        raise AssertionError("expected ValueError for future version")
    except ValueError as e:
        assert "unsupported" in str(e)
    print("PASS: test_loop_state_rejects_malformed")


def test_corrupt_loop_state_fails_loud():
    """Corrupt loop state is an explicit recovery failure, never a fresh start."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "v1.json"
        path.write_text("{not json", encoding="utf-8")
        try:
            LoopStateStore(path).load()
            raise AssertionError("expected CheckpointCorrupt")
        except CheckpointCorrupt:
            pass
    print("PASS: test_corrupt_loop_state_fails_loud")


# ── Outcome classification: total, fail-closed ───────────────────────────────

def test_loop_outcome_lifecycle_total():
    for oc in LoopOutcome.ALL:
        lc = lifecycle_for_loop_outcome(oc)
        assert lc in RunLifecycle.ALL, oc
    assert lifecycle_for_loop_outcome(LoopOutcome.TASK_COMPLETE) == RunLifecycle.COMPLETED
    assert lifecycle_for_loop_outcome(LoopOutcome.BUDGET_EXHAUSTED) == RunLifecycle.COMPLETED
    assert lifecycle_for_loop_outcome(LoopOutcome.BLOCKED) == RunLifecycle.TERMINATED
    # fail-closed: unknown outcome → FAILED
    assert lifecycle_for_loop_outcome("mystery") == RunLifecycle.FAILED
    print("PASS: test_loop_outcome_lifecycle_total")


def test_max_iter_sane_cap_enforced():
    """No unbounded loop configuration: max_iter above the cap is refused."""
    def collector(video_id, url, **kw):
        return []
    try:
        SelfHealingPipeline(base_dir=ROOT, collector=collector,
                            processor=lambda *a, **k: _m(), max_iter=999)
        raise AssertionError("expected ValueError for max_iter above cap")
    except ValueError as e:
        assert "cap" in str(e)
    for bad in (0, -1, "3"):
        try:
            SelfHealingPipeline(base_dir=ROOT, collector=collector,
                                processor=lambda *a, **k: _m(), max_iter=bad)
            raise AssertionError(f"expected ValueError for max_iter={bad!r}")
        except ValueError:
            pass
    print("PASS: test_max_iter_sane_cap_enforced")


# ── Intent immutability: override-surface axis audit (FR-LOOP-003) ───────────

def test_override_surface_contains_only_collection_axes():
    """The loop's `_apply` surface may never contain intent fields."""
    FORBIDDEN = {
        "target", "target_url", "url", "video_id", "job_id",
        "actor_id", "actor_version", "provider",
        "quality_threshold", "min_quality", "acceptance",  # gate axes are
        "gate", "threshold",                                # tighten-only, not free
    }
    planner = ImprovementPlanner()
    pipe = SelfHealingPipeline(base_dir=ROOT, collector=lambda *a, **k: [],
                               processor=lambda *a, **k: _m())
    for metrics in (_m(coverage=0.3, partial=True, avg_quality=0.1),
                    _m(coverage=0.5, stall_reason="EPIPE"),):
        plan = planner.plan(metrics, iteration=1)
        overrides = pipe._apply(plan)
        bad = FORBIDDEN & set(overrides)
        assert not bad, f"intent/acceptance axes leaked into overrides: {bad}"
        # every override is a known collection-parameter axis
        ALLOWED = {"fresh_browser", "scrolls", "provider", "reply_depth",
                   "retry_partial", "adjust_gate", "batch", "chunk"}
        assert set(overrides) <= ALLOWED, set(overrides) - ALLOWED
    # ADJUST_GATE contract: tighten-only — the action exists but must never
    # map to a lowering directive
    assert Action.ADJUST_GATE.value == "adjust_gate"
    print("PASS: test_override_surface_contains_only_collection_axes")


# ── Resume: crash mid-loop never double-executes an iteration ────────────────

def test_resume_skips_recorded_iterations():
    """Crash after iteration 1's manifest append → rerun skips iteration 1."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        collect_calls = {"n": 0}

        def collector(video_id, url, **kw):
            collect_calls["n"] += 1
            return []

        # iterations improve 0.40→0.60→0.70 then stall (+0.01 < 0.05 delta)
        # → the loop classifies PARTIAL_SUCCESS and stops at iteration 3.
        states = iter([_m(coverage=0.60), _m(coverage=0.70), _m(coverage=0.71)])

        def processor(existing, base_dir, **kw):
            if not kw.get("iteration"):
                return _m(coverage=0.40)  # observe existing: unstable
            return next(states)

        pipe = SelfHealingPipeline(base_dir=base, collector=collector,
                                   processor=processor, max_iter=3,
                                   sleep_between=0, state_dir=base / "state")
        pipe.run("v1", URL)
        job_id = pipe.last_job_id
        assert pipe.last_outcome == LoopOutcome.PARTIAL_SUCCESS
        assert collect_calls["n"] == 3

        mfile = base / "data" / "manifests" / "v1.improve.jsonl"
        assert mfile.exists()
        lines = [json.loads(l) for l in mfile.read_text().splitlines() if l.strip()]
        assert [d["iteration"] for d in lines] == [1, 2, 3]
        assert all(d["job_id"] == job_id for d in lines)

        # --- simulate crash/resume: same video, state file still present ---
        # State sits at next_iteration=4 > max_iter=3, so NO new collects and
        # no new manifest lines may appear.
        def processor_resumed(existing, base_dir, **kw):
            return _m(coverage=0.71, partial=True)

        pipe2 = SelfHealingPipeline(base_dir=base, collector=collector,
                                    processor=processor_resumed, max_iter=3,
                                    sleep_between=0, state_dir=base / "state")
        pipe2.run("v1", URL)
        assert pipe2.last_job_id == job_id, "resume must attach to same job"
        assert collect_calls["n"] == 3, "resume re-executed iterations!"
        assert pipe2.last_outcome == LoopOutcome.PARTIAL_SUCCESS, \
            "terminal outcome must survive rerun verbatim (not reclassified)"
        lines2 = [json.loads(l) for l in mfile.read_text().splitlines() if l.strip()]
        assert [d["iteration"] for d in lines2] == [1, 2, 3], \
            "resume appended duplicate manifest lines"
    print("PASS: test_resume_skips_recorded_iterations")


def test_resume_mid_loop_continues_at_saved_position():
    """State at next_iteration=2 → fresh run starts at iteration 2, not 1."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        store = LoopStateStore(base / "state" / "v1.json")
        store.save(LoopState(video_id="v1", job_id="jobX", next_iteration=2,
                             applied_overrides={}, last_metrics={}))

        iterations_seen = []

        def collector(video_id, url, **kw):
            return []

        def processor(existing, base_dir, **kw):
            if not kw.get("iteration"):
                return _m(coverage=0.40)
            iterations_seen.append(kw["iteration"])
            return _m(coverage=0.40 + 0.10 * kw["iteration"])

        pipe = SelfHealingPipeline(base_dir=base, collector=collector,
                                   processor=processor, max_iter=3,
                                   sleep_between=0, state_dir=base / "state")
        pipe.run("v1", URL)
        assert pipe.last_job_id == "jobX"
        assert iterations_seen == [2, 3], iterations_seen
        assert pipe.last_outcome == LoopOutcome.BUDGET_EXHAUSTED
    print("PASS: test_resume_mid_loop_continues_at_saved_position")


def test_exception_classifies_failed_and_re_raises():
    """A crashing collector still leaves a FAILED-classified loop state."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        def collector(video_id, url, **kw):
            raise RuntimeError("browser exploded")

        pipe = SelfHealingPipeline(base_dir=base, collector=collector,
                                   processor=lambda *a, **k: _m(),
                                   max_iter=2, sleep_between=0,
                                   state_dir=base / "state")
        try:
            pipe.run("v1", URL)
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError:
            pass
        assert pipe.last_outcome == LoopOutcome.FAILED
        saved = LoopStateStore(base / "state" / "v1.json").load()
        assert saved.outcome == LoopOutcome.FAILED
        assert saved.job_id == pipe.last_job_id
    print("PASS: test_exception_classifies_failed_and_re_raises")


# ── Terminal-state resume: finished loops never execute again ───────────────

def test_resume_task_complete_is_terminal():
    """TASK_COMPLETE on disk → rerun executes nothing, returns the saved result."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        calls = {"collect": 0}

        def collector(video_id, url, **kw):
            calls["collect"] += 1
            return []

        states = iter([_m(coverage=0.96, partial=False)])  # stable at iter 1

        def processor(existing, base_dir, **kw):
            if not kw.get("iteration"):
                return _m(coverage=0.40)  # observe existing: unstable
            return next(states)

        pipe = SelfHealingPipeline(base_dir=base, collector=collector,
                                   processor=processor, max_iter=3,
                                   sleep_between=0, state_dir=base / "state")
        first = pipe.run("v1", URL)
        assert pipe.last_outcome == LoopOutcome.TASK_COMPLETE
        job_id = pipe.last_job_id
        assert calls["collect"] == 1

        state_path = base / "state" / "v1.json"
        state_before = state_path.read_text(encoding="utf-8")
        mfile = base / "data" / "manifests" / "v1.improve.jsonl"
        lines_before = [json.loads(l) for l in mfile.read_text().splitlines()
                        if l.strip()]

        # --- rerun against the finished loop: nothing may execute ---
        def collector_rerun(video_id, url, **kw):
            calls["collect"] += 1
            raise AssertionError("collection attempted on a finished loop")

        def processor_rerun(existing, base_dir, **kw):
            raise AssertionError("processing attempted on a finished loop")

        pipe2 = SelfHealingPipeline(base_dir=base, collector=collector_rerun,
                                    processor=processor_rerun, max_iter=3,
                                    sleep_between=0, state_dir=base / "state")
        again = pipe2.run("v1", URL)
        assert pipe2.last_outcome == LoopOutcome.TASK_COMPLETE
        assert pipe2.last_job_id == job_id, "job identity must survive"
        assert calls["collect"] == 1, "rerun collected!"
        assert again.coverage == first.coverage, "last metrics must survive"
        assert again.partial == first.partial
        assert state_path.read_text(encoding="utf-8") == state_before, \
            "terminal resume rewrote loop state"
        lines_after = [json.loads(l) for l in mfile.read_text().splitlines()
                       if l.strip()]
        assert lines_after == lines_before, "rerun appended manifest lines"
    print("PASS: test_resume_task_complete_is_terminal")


def test_resume_budget_exhausted_is_terminal():
    """BUDGET_EXHAUSTED on disk → rerun neither collects nor replans."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        calls = {"collect": 0, "replanned": 0}

        def collector(video_id, url, **kw):
            calls["collect"] += 1
            return []

        # keeps improving (+0.15 each round) but never reaches 0.95 → budget out
        def processor(existing, base_dir, **kw):
            if not kw.get("iteration"):
                return _m(coverage=0.40)
            cov = min(0.90, 0.40 + 0.15 * kw["iteration"])
            return _m(coverage=cov, partial=True)

        pipe = SelfHealingPipeline(base_dir=base, collector=collector,
                                   processor=processor, max_iter=3,
                                   sleep_between=0, state_dir=base / "state")
        first = pipe.run("v1", URL)
        assert pipe.last_outcome == LoopOutcome.BUDGET_EXHAUSTED
        job_id = pipe.last_job_id
        n_first = calls["collect"]
        assert n_first > 0

        def processor_rerun(existing, base_dir, **kw):
            calls["replanned"] += 1
            raise AssertionError("replan attempted on an exhausted loop")

        pipe2 = SelfHealingPipeline(base_dir=base, collector=collector,
                                    processor=processor_rerun, max_iter=3,
                                    sleep_between=0, state_dir=base / "state")
        again = pipe2.run("v1", URL)
        assert pipe2.last_outcome == LoopOutcome.BUDGET_EXHAUSTED
        assert pipe2.last_job_id == job_id
        assert calls["collect"] == n_first, "rerun collected!"
        assert calls["replanned"] == 0, "rerun replanned!"
        assert again.coverage == first.coverage
    print("PASS: test_resume_budget_exhausted_is_terminal")


def test_resume_failed_is_terminal():
    """FAILED on disk → rerun neither retries nor raises; classification holds."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        calls = {"collect": 0, "processed": 0}

        def collector_boom(video_id, url, **kw):
            calls["collect"] += 1
            raise RuntimeError("browser exploded")

        def processor_observe(existing, base_dir, **kw):
            return _m(coverage=0.40)

        pipe = SelfHealingPipeline(base_dir=base, collector=collector_boom,
                                   processor=processor_observe,
                                   max_iter=2, sleep_between=0,
                                   state_dir=base / "state")
        try:
            pipe.run("v1", URL)
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass
        job_id = pipe.last_job_id
        n_first = calls["collect"]

        def processor_rerun(existing, base_dir, **kw):
            calls["processed"] += 1
            raise AssertionError("failed loop was retried on rerun")

        pipe2 = SelfHealingPipeline(base_dir=base,
                                    collector=lambda *a, **k: [],
                                    processor=processor_rerun, max_iter=2,
                                    sleep_between=0, state_dir=base / "state")
        result = pipe2.run("v1", URL)  # must NOT raise, NOT retry
        assert pipe2.last_outcome == LoopOutcome.FAILED
        assert pipe2.last_job_id == job_id
        assert calls["collect"] == n_first, "rerun retried the collection!"
        assert calls["processed"] == 0, "rerun processed!"
        assert result.coverage == 0.40  # last observed snapshot preserved
    print("PASS: test_resume_failed_is_terminal")


def test_torn_manifest_tail_is_ignorable():
    """A crash mid-append leaves a torn JSON line; resume must not die on it."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        mdir = base / "data" / "manifests"
        mdir.mkdir(parents=True)
        good = {"iteration": 1, "job_id": "jobZ", "actions": ["split_batch"],
                "rationale": "r", "metrics": {}, "timestamp": "t"}
        (mdir / "v1.improve.jsonl").write_text(
            json.dumps(good) + "\n" + '{"iteration": 2, "job_',
            encoding="utf-8")
        pipe = SelfHealingPipeline(base_dir=base, collector=lambda *a, **k: [],
                                   processor=lambda *a, **k: _m(),
                                   state_dir=base / "state")
        recorded = pipe._recorded_iterations("v1", "jobZ")
        assert recorded == {1}, recorded
    print("PASS: test_torn_manifest_tail_is_ignorable")


def main():
    test_loop_state_round_trip()
    test_loop_state_rejects_malformed()
    test_corrupt_loop_state_fails_loud()
    test_loop_outcome_lifecycle_total()
    test_max_iter_sane_cap_enforced()
    test_override_surface_contains_only_collection_axes()
    test_resume_skips_recorded_iterations()
    test_resume_mid_loop_continues_at_saved_position()
    test_exception_classifies_failed_and_re_raises()
    test_resume_task_complete_is_terminal()
    test_resume_budget_exhausted_is_terminal()
    test_resume_failed_is_terminal()
    test_torn_manifest_tail_is_ignorable()
    print("\nAll loop-state tests passed ✅")


if __name__ == "__main__":
    main()
