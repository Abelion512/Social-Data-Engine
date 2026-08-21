#!/usr/bin/env python3
"""
Deterministic tests for the provider-independent AcquisitionRuntime.

Proves (no browser, no network, stdlib only):
 1. A provider can start a run                       → dataset + checkpoint written
 2. Checkpoint can be written                        → atomic, restorable state
 3. Run can resume                                   → continues from cursor, no dupes
 4. Retry state survives                             → retry_count/metrics in checkpoint
 5. Termination reason is preserved                  → raw reason + outcome category
 6. Metrics are recorded                             → pages/items/retries/duration
 7. Duplicate pages/items do not corrupt state       → unique ids only, counts correct
 8. TikTok-shaped payloads run on the runtime        → the proven 198+ page script
 9. A second fake provider runs unmodified           → runtime untouched per provider
10. Resuming a COMPLETED run reports persisted count → no provider call, no rewrite

Run:  python -m pytest tests/test_acquisition_runtime.py -v
      python tests/test_acquisition_runtime.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime import (
    AcquisitionActor,
    AcquisitionRuntime,
    PageResult,
    RunContext,
    RunOptions,
    Outcome,
    classify_termination,
)


# ── Fake providers ────────────────────────────────────────────────────────────

class FakeActor(AcquisitionActor):
    """Scriptable actor: returns a fixed sequence of PageResults."""

    def __init__(self, pages: List[PageResult], id_key: str = "item_id",
                 provider_name: str = "fake"):
        self.provider_name = provider_name
        self.id_key = id_key
        self.pages = list(pages)
        self.calls: List[tuple] = []

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        self.calls.append((cursor, page_index))
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            return self.pages[idx]
        return PageResult(items=[], next_cursor=cursor, has_more=False)


def items(prefix: str, start: int, count: int) -> List[dict]:
    return [{"item_id": f"{prefix}_{i}", "text": f"item {i}"}
            for i in range(start, start + count)]


def make_ctx(tmpdir: str, job_id: str = None, provider: str = "fake") -> RunContext:
    return RunContext.create(
        provider=provider,
        target_url="https://example.test/target/1",
        job_id=job_id or "job_test",
        state_dir=str(Path(tmpdir) / "state"),
        data_dir=str(Path(tmpdir) / "data"),
    )


def read_lines(path) -> List[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def run(coro):
    return asyncio.run(coro)


# ── 1 + 2. Start a run; checkpoint written ────────────────────────────────────

def test_actor_can_start_run_and_checkpoint_is_written():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("a", 0, 50), next_cursor=50, has_more=True),
            PageResult(items=items("a", 50, 50), next_cursor=100, has_more=False),
        ])
        summary = run(AcquisitionRuntime().run(actor, ctx))

        assert summary.termination_reason == "has_more_false"
        assert summary.outcome == Outcome.SUCCESS
        assert summary.items_seen == 100

        # Dataset written, exactly 100 unique records
        lines = read_lines(ctx.dataset_path)
        assert len(lines) == 100
        assert len({r["item_id"] for r in lines}) == 100

        # Checkpoint written, restorable and complete
        ckpt = json.loads(Path(ctx.checkpoint_path).read_text())
        assert ckpt["provider"] == "fake"
        assert ckpt["status"] == "done"
        assert ckpt["run_id"] == ctx.run_id
        assert ckpt["pagination"]["cursor"] == 100
        assert ckpt["pagination"]["termination_reason"] == "has_more_false"
        assert ckpt["metrics"]["pages_succeeded"] == 2


# ── 3. Resume from checkpoint ─────────────────────────────────────────────────

def test_run_resumes_from_checkpoint_without_duplicates():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("a", 0, 50), next_cursor=50, has_more=True),
            PageResult(items=items("a", 50, 50), next_cursor=100, has_more=False),
        ])

        # First invocation: stop after 1 page via max_pages cap
        s1 = run(AcquisitionRuntime().run(
            actor, ctx, RunOptions(max_pages=1)))
        assert s1.termination_reason == "max_pages_reached"
        assert s1.outcome == Outcome.CAP_REACHED
        assert s1.items_written == 50

        # Second invocation resumes from cursor=50 and completes
        s2 = run(AcquisitionRuntime().run(
            actor, ctx, RunOptions(resume=True)))
        assert s2.resumed is True
        assert s2.termination_reason == "has_more_false"
        assert s2.outcome == Outcome.SUCCESS

        # Union of both invocations = full set, zero duplicates on disk
        lines = read_lines(ctx.dataset_path)
        ids = [r["item_id"] for r in lines]
        assert len(ids) == 100
        assert len(set(ids)) == 100
        assert s2.items_seen == 100


# ── 4. Retry state survives a crash/stop ──────────────────────────────────────

def test_retry_state_survives_in_checkpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        # A run that fails then RECOVERS WITH CURSOR ADVANCE records retry
        # evidence; the advance branch resets retry_count (shared semantics).
        actor2 = FakeActor([
            PageResult(error="connection reset", error_type="fetch_failure"),
            PageResult(items=items("a", 0, 30), next_cursor=30, has_more=True),
        ])
        ctx2 = make_ctx(tmpdir, job_id="job_retry")
        s = run(AcquisitionRuntime().run(actor2, ctx2))
        assert s.metrics["fetch_errors"] >= 1

        # Retry evidence persisted in the checkpoint
        ckpt = json.loads(Path(ctx2.checkpoint_path).read_text())
        pag = ckpt["pagination"]
        assert ckpt["metrics"]["fetch_errors"] >= 1
        # The successful page after recovery resets retry_count (recovery semantics)
        assert pag["retry_count"] == 0
        assert pag["cursor"] == 30

        # Prove a *pending* retry survives: fresh run interrupted mid-retry
        ctx3 = make_ctx(tmpdir, job_id="job_pending")
        flaky = FakeActor([
            PageResult(error="gateway timeout", error_type="fetch_failure"),
        ])
        sp = run(AcquisitionRuntime().run(flaky, ctx3, RunOptions(max_pages=1)))
        # Loop never advanced a page; but the failure WAS committed to checkpoint
        ckpt3 = json.loads(Path(ctx3.checkpoint_path).read_text())
        assert ckpt3["metrics"]["fetch_errors"] == 1
        assert ckpt3["pagination"]["retry_count"] == 1

        # Reload from disk like a resumed process would
        from src.runtime import CheckpointStore, PaginationState
        restored = PaginationState.from_dict(ckpt3["pagination"])
        # Pending retry evidence survives the serialization round-trip:
        assert restored.retry_count == 1
        assert restored.metrics.fetch_errors == 1
        _ = sp  # silence unused


# ── 5. Termination reason preserved ──────────────────────────────────────────

def test_termination_reason_and_outcome_preserved():
    with tempfile.TemporaryDirectory() as tmpdir:
        # auth/block → terminal, reason + category preserved end-to-end
        ctx = make_ctx(tmpdir, job_id="job_auth")
        actor = FakeActor([
            PageResult(error="captcha challenge", error_type="auth"),
        ])
        s = run(AcquisitionRuntime().run(actor, ctx))
        assert s.termination_reason == "auth_blocked"
        assert s.outcome == Outcome.AUTH_BLOCKED

        # Stall → pagination_stall outcome
        ctx2 = make_ctx(tmpdir, job_id="job_stall")
        stall_actor = FakeActor([
            PageResult(items=items("s", 0, 1), next_cursor=10, has_more=True),
            PageResult(items=items("s", 1, 1), next_cursor=10, has_more=True),
            PageResult(items=items("s", 2, 1), next_cursor=10, has_more=True),
            PageResult(items=items("s", 3, 1), next_cursor=10, has_more=True),
        ])
        s2 = run(AcquisitionRuntime().run(stall_actor, ctx2, RunOptions(max_stalls=3)))
        assert s2.termination_reason == "cursor_stalled"
        assert s2.outcome == Outcome.PAGINATION_STALL

        # Item cap → user/configured-cap outcome, legacy reason string intact
        ctx3 = make_ctx(tmpdir, job_id="job_cap")
        cap_actor = FakeActor([
            PageResult(items=items("c", 0, 50), next_cursor=50, has_more=True),
        ])
        s3 = run(AcquisitionRuntime().run(cap_actor, ctx3, RunOptions(max_items=25)))
        assert s3.termination_reason == "max_comments_reached"  # legacy-compatible string
        assert s3.outcome == Outcome.CAP_REACHED

        # Retry budget exhausted → retryable-failure classification
        ctx4 = make_ctx(tmpdir, job_id="job_fail")
        fail_actor = FakeActor([
            PageResult(error="e1", error_type="fetch_failure"),
            PageResult(error="e2", error_type="fetch_failure"),
            PageResult(error="e3", error_type="fetch_failure"),
        ])
        s4 = run(AcquisitionRuntime().run(fail_actor, ctx4, RunOptions(max_retries=3)))
        assert s4.termination_reason == "max_retries_exceeded"
        assert s4.outcome == Outcome.RETRYABLE_FAILURE

        # classify_termination covers the taxonomy directly
        assert classify_termination("has_more_false") == Outcome.SUCCESS
        assert classify_termination(None) == Outcome.PERMANENT_FAILURE
        assert classify_termination("something_new") == Outcome.PERMANENT_FAILURE


# ── 6. Metrics recorded ──────────────────────────────────────────────────────

def test_metrics_are_recorded():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(error="timeout", error_type="fetch_failure"),   # 1 failed attempt
            PageResult(items=items("m", 0, 40), next_cursor=40, has_more=True),
            PageResult(items=items("m", 40, 20), next_cursor=60, has_more=False),
        ])
        s = run(AcquisitionRuntime().run(actor, ctx))

        m = s.metrics
        assert m["pages_attempted"] == 3          # every fetch attempt counted
        assert m["pages_succeeded"] == 2
        assert m["items_collected"] == 60         # gross unique items across pages
        assert m["items_unique"] == 60
        assert m["fetch_errors"] == 1
        assert m["duration_seconds"] >= 0.0
        assert m["ended_at"] is not None
        # Metrics are identical in the checkpoint (provenance)
        ckpt = json.loads(Path(ctx.checkpoint_path).read_text())
        assert ckpt["metrics"]["pages_attempted"] == 3
        assert ckpt["metrics"]["fetch_errors"] == 1


# ── 7. Duplicates do not corrupt dataset or metrics ──────────────────────────

def test_duplicate_pages_and_items_do_not_corrupt_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("d", 0, 10), next_cursor=10, has_more=True),
            # Entirely duplicated page (same ids), same cursor returned
            PageResult(items=items("d", 0, 10), next_cursor=10, has_more=True),
            PageResult(items=items("d", 5, 15), next_cursor=20, has_more=False),
        ])
        s = run(AcquisitionRuntime().run(actor, ctx))

        lines = read_lines(ctx.dataset_path)
        ids = [r["item_id"] for r in lines]
        assert len(ids) == len(set(ids)) == 20     # d_0..d_19 exactly once each
        assert sorted(ids) == sorted({f"d_{i}" for i in range(20)})
        assert s.items_seen == 20
        # Deduplicated volume is visible in metrics
        assert s.metrics["items_deduplicated"] >= 10
        # Second identical run with resume writes nothing new
        s2 = run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))
        assert read_lines(ctx.dataset_path).__len__() == 20
        assert s2.items_written == 0


# ── 8. TikTok-shaped payloads run on the runtime ─────────────────────────────

class TikTokShapedActor(AcquisitionActor):
    """Adapter proving TikTok's raw API response shape runs unmodified on the
    runtime: {"comments": [...], "cursor": N, "has_more": 0|1}, cid-keyed."""

    provider_name = "tiktok-shaped"

    def __init__(self, api_pages: List[dict]):
        self.api_pages = api_pages
        self.id_key = "cid"
        self.sent_cursors: List[int] = []
        self.calls = 0

    def initial_cursor(self):
        return 0

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        # Pages are served PER CALL (not per page_index): a transient empty
        # page does not advance the pagination index, so the runtime retries
        # the same cursor — mirroring the live collector's mock exactly.
        self.sent_cursors.append(cursor)
        idx = self.calls
        self.calls += 1
        if idx < len(self.api_pages):
            pg = self.api_pages[idx]
            return PageResult(
                items=pg["comments"],
                next_cursor=pg["cursor"],
                has_more=bool(pg["has_more"]),
            )
        return PageResult(items=[], next_cursor=cursor, has_more=False)


def test_tiktok_shaped_actor_runs_on_runtime_past_198_boundary():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Same proven live-pagination script that broke the ~198 boundary:
        # 100 → 98 (=198) → transient empty → final 50 with has_more=0.
        api_pages = [
            {"comments": [{"cid": f"c_{i}", "text": f"x{i}"} for i in range(100)],
             "cursor": 100, "has_more": 1},
            {"comments": [{"cid": f"c_{i}", "text": f"x{i}"} for i in range(100, 198)],
             "cursor": 198, "has_more": 1},
            {"comments": [], "cursor": 198, "has_more": 1},   # transient empty page
            {"comments": [{"cid": f"c_{i}", "text": f"x{i}"} for i in range(198, 248)],
             "cursor": 248, "has_more": 0},
        ]
        ctx = RunContext.create(
            provider="tiktok-shaped",
            target_url="https://www.tiktok.com/@u/video/123",
            job_id="job_tt_shape",
            state_dir=str(Path(tmpdir) / "state"),
            data_dir=str(Path(tmpdir) / "data"),
        )
        actor = TikTokShapedActor(api_pages)
        s = run(AcquisitionRuntime().run(actor, ctx))

        assert s.termination_reason == "has_more_false"
        assert s.outcome == Outcome.SUCCESS
        assert s.items_seen == 248                      # past the 198 boundary
        assert actor.sent_cursors == [0, 100, 198, 198]  # empty page retries same cursor
        lines = read_lines(ctx.dataset_path)
        cids = [r["cid"] for r in lines]
        assert len(cids) == len(set(cids)) == 248
        assert s.metrics["empty_pages"] == 1
        assert s.metrics["pages_succeeded"] == 3


# ── 9. A second provider needs zero runtime changes ──────────────────────────

# ── 10. Resume of an already-completed checkpoint ────────────────────────────

def test_resume_completed_checkpoint_reports_persisted_items_seen():
    """Regression: the terminal-checkpoint resume path used to return before
    dataset.load_seen(), reporting items_seen=0 despite a populated dataset."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("a", 0, 50), next_cursor=50, has_more=True),
            PageResult(items=items("a", 50, 50), next_cursor=100, has_more=False),
        ])
        s1 = run(AcquisitionRuntime().run(actor, ctx))
        assert s1.items_seen == 100
        assert s1.termination_reason == "has_more_false"

        calls_before = len(actor.calls)
        dataset_before = read_lines(ctx.dataset_path)

        # Resume the ALREADY-COMPLETED job.
        s2 = run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))

        assert s2.resumed is True
        # Provider must NOT be called again
        assert len(actor.calls) == calls_before
        # Dataset must not be modified (byte-identical records, same count)
        dataset_after = read_lines(ctx.dataset_path)
        assert dataset_after == dataset_before
        assert len(dataset_after) == 100
        # Termination reason preserved from the checkpoint
        assert s2.termination_reason == s1.termination_reason == "has_more_false"
        assert s2.outcome == Outcome.SUCCESS
        # Nothing new written this invocation…
        assert s2.items_written == 0
        # …but items_seen reflects the ACTUAL persisted unique records (≠ 0)
        assert s2.items_seen == 100


def test_second_provider_runs_unmodified():
    with tempfile.TemporaryDirectory() as tmpdir:

        ctx_b = make_ctx(tmpdir, job_id="job_other", provider="otherplatform")

        class OtherPlatformActor(AcquisitionActor):
            provider_name = "otherplatform"
            id_key = "post_pk"

            def __init__(self):
                self.pages = [
                    [{"post_pk": f"p{i}", "body": "b"} for i in range(5)],
                    [],
                ]

            async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
                data = self.pages[page_index] if page_index < len(self.pages) else []
                return PageResult(items=data, next_cursor=page_index + 1,
                                  has_more=bool(data))

        rt = AcquisitionRuntime()   # same runtime instance, same code path
        ctx_a = make_ctx(tmpdir, job_id="job_fake")
        s_a = run(rt.run(FakeActor([
            PageResult(items=items("f", 0, 3), next_cursor=1, has_more=False),
        ]), ctx_a))

        s_b = run(rt.run(OtherPlatformActor(), ctx_b))

        assert s_a.provider == "fake"
        assert s_b.provider == "otherplatform"
        assert s_b.termination_reason == "has_more_false"
        b_lines = read_lines(ctx_b.dataset_path)
        assert [r["post_pk"] for r in b_lines] == [f"p{i}" for i in range(5)]


if __name__ == "__main__":
    tests = [
        test_actor_can_start_run_and_checkpoint_is_written,
        test_run_resumes_from_checkpoint_without_duplicates,
        test_retry_state_survives_in_checkpoint,
        test_termination_reason_and_outcome_preserved,
        test_metrics_are_recorded,
        test_duplicate_pages_and_items_do_not_corrupt_state,
        test_tiktok_shaped_actor_runs_on_runtime_past_198_boundary,
        test_second_provider_runs_unmodified,
        test_resume_completed_checkpoint_reports_persisted_items_seen,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__} ✅")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__} ❌ : {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__} ❌ : {type(e).__name__}: {e}")
            failed += 1
    print(f"\nAcquisition Runtime Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
