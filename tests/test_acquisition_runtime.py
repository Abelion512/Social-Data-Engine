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
11. max_items is a HARD cap                          → oversized pages truncated,
                                                       dupes never consume budget,
                                                       resume respects remaining cap
12. Crash recovery at the commit boundary (simulated) → dataset valid, old
                                                       checkpoint readable, replay
                                                       without duplicates

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
    CheckpointStore,
    PageResult,
    PaginationState,
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


# ── 11. HARD item cap: oversized page never persists past max_items ─────────

def test_max_items_hard_limit_oversized_page():
    """max_items=25 with a 50-unique-item page → exactly 25 persisted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("a", 0, 50), next_cursor=50, has_more=True),
        ])
        s = run(AcquisitionRuntime().run(actor, ctx, RunOptions(max_items=25)))

        assert s.items_seen == 25                       # hard cap respected
        assert s.termination_reason == "max_comments_reached"
        assert s.outcome == Outcome.CAP_REACHED
        lines = read_lines(ctx.dataset_path)
        assert len(lines) == 25                         # dataset truncated too
        assert len({r["item_id"] for r in lines}) == 25
        # checkpoint holds the SAME count as the dataset
        ckpt = json.loads(Path(ctx.checkpoint_path).read_text())
        assert ckpt["items_seen"] == 25
        assert ckpt["pagination"]["items_seen"] == 25
        # provider was NOT re-fetched after the cap (single call total)
        assert len(actor.calls) == 1


def test_resume_respects_remaining_cap():
    """Dataset already has 20 (capped run); resume with max_items=25 and a
    50-item page → exactly 5 NEW items persisted, then cap terminates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("a", 0, 50), next_cursor=50, has_more=True),
            PageResult(items=items("a", 50, 50), next_cursor=100, has_more=False),
        ])
        s1 = run(AcquisitionRuntime().run(actor, ctx, RunOptions(max_items=20)))
        assert s1.items_seen == 20
        assert s1.termination_reason == "max_comments_reached"

        calls_before = len(actor.calls)
        s2 = run(AcquisitionRuntime().run(
            actor, ctx, RunOptions(max_items=25, resume=True)))

        assert s2.resumed is True
        assert s2.items_written == 5                    # only the remaining budget
        assert s2.items_seen == 25                      # never exceeds the new cap
        assert s2.termination_reason == "max_comments_reached"
        lines = read_lines(ctx.dataset_path)
        assert len(lines) == 25
        assert len({r["item_id"] for r in lines}) == 25
        ckpt = json.loads(Path(ctx.checkpoint_path).read_text())
        assert ckpt["items_seen"] == 25 == len(lines)
        assert len(actor.calls) > calls_before          # continuation fetched once more


def test_duplicate_heavy_page_does_not_consume_cap():
    """Dupes are filtered BEFORE the budget math: a 40-item page with 30 dupes
    persists its 20 new items without wasting any of max_items=25."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        first = items("d", 0, 15)
        second = first[:10] + items("d", 15, 30)       # 10 dupes + 30 new = 40 gross
        actor = FakeActor([
            PageResult(items=first, next_cursor=50, has_more=True),
            PageResult(items=second, next_cursor=90, has_more=False),
        ])
        s = run(AcquisitionRuntime().run(actor, ctx, RunOptions(max_items=25)))

        # 15 + 30 unique available; cap 25 → exactly 25 persisted, dupes free
        assert s.items_seen == 25
        ids = {r["item_id"] for r in read_lines(ctx.dataset_path)}
        assert len(ids) == 25
        assert s.metrics["items_deduplicated"] >= 10    # dedup still recorded


def test_checkpoint_matches_dataset_at_cap():
    """After a mid-page cap stop, checkpoint item count == dataset record count
    AND pagination state is restorable at that exact position."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("k", 0, 40), next_cursor=40, has_more=True),
        ])
        run(AcquisitionRuntime().run(actor, ctx, RunOptions(max_items=12)))

        lines = read_lines(ctx.dataset_path)
        ckpt_raw = json.loads(Path(ctx.checkpoint_path).read_text())
        assert ckpt_raw["status"] == "done"
        assert ckpt_raw["items_seen"] == len(lines) == 12
        restored = PaginationState.from_dict(ckpt_raw["pagination"])
        assert restored.items_seen == 12
        assert restored.termination_reason == "max_comments_reached"


# ── 12. Crash recovery at the commit boundary (simulated, no process kill) ──
# These inject OSError at the checkpoint-commit boundary. They prove ordering
# + replay correctness, NOT power-loss durability.

def test_crash_dataset_persisted_checkpoint_commit_fails():
    """A: append succeeded, checkpoint save raised → old checkpoint stays
    readable, dataset stays valid, resume replays without duplicates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        actor = FakeActor([
            PageResult(items=items("x", 0, 30), next_cursor=30, has_more=True),
            PageResult(items=items("x", 30, 30), next_cursor=60, has_more=False),
        ])
        rt = AcquisitionRuntime(print_fn=lambda *_: None)
        run(rt.run(actor, ctx, RunOptions(max_items=35)))   # stops mid-run at cap
        good_lines = read_lines(ctx.dataset_path)
        assert len(good_lines) == 35
        old_ckpt = json.loads(Path(ctx.checkpoint_path).read_text())

        original_save = CheckpointStore.save
        CheckpointStore.save = lambda self, data: (_ for _ in ()).throw(
            OSError("simulated disk full during commit"))
        try:
            crashed = run(rt.run(
                actor, ctx, RunOptions(max_items=60, resume=True)))
            raise AssertionError("expected the commit failure to propagate")
        except OSError:
            pass
        finally:
            CheckpointStore.save = original_save

        # Dataset survived intact and valid JSONL
        after_crash = read_lines(ctx.dataset_path)
        assert len(after_crash) >= len(good_lines)
        ids = [r["item_id"] for r in after_crash]
        assert len(ids) == len(set(ids))                # no torn/dup records
        # Old checkpoint remains readable with its previous content
        still = json.loads(Path(ctx.checkpoint_path).read_text())
        assert still["job_id"] == old_ckpt["job_id"]
        assert still["updated_at"] == old_ckpt["updated_at"]

        # Clean resume: replay does not duplicate anything already persisted
        s3 = run(AcquisitionRuntime(print_fn=lambda *_: None).run(
            actor, ctx, RunOptions(max_items=60, resume=True)))
        final_lines = read_lines(ctx.dataset_path)
        final_ids = {r["item_id"] for r in final_lines}
        assert len(final_ids) == len(final_lines)       # zero duplicate rows
        assert s3.items_seen == len(final_lines) == 35  # source exhausted at 35
        assert s3.termination_reason == "has_more_false"


def test_crash_checkpoint_temp_write_fails_previous_readable():
    """B: temp-write fails on the very first commit → previously seeded valid
    checkpoint remains readable and untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_ctx(tmpdir)
        store = CheckpointStore(ctx.checkpoint_path)
        seed = {"job_id": ctx.job_id, "status": "seeded", "marker": True,
                "pagination": {"cursor": 7}}
        store.save(seed)

        actor = FakeActor([
            PageResult(items=items("y", 0, 10), next_cursor=10, has_more=False),
        ])
        original_save = CheckpointStore.save
        def _boom(self, data):
            raise OSError("simulated temp-write failure")
        CheckpointStore.save = _boom
        try:
            run(AcquisitionRuntime(print_fn=lambda *_: None).run(actor, ctx))
            raise AssertionError("expected commit failure to propagate")
        except OSError:
            pass
        finally:
            CheckpointStore.save = original_save

        loaded = store.load()
        assert loaded == seed                           # previous ckpt intact
        # (no fresh-run rerun here on purpose: rerunning without resume over a
        # populated dataset path is the documented fresh-run limitation)


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
        ckpt_bytes_before = Path(ctx.checkpoint_path).read_text(encoding="utf-8")

        # Resume the ALREADY-COMPLETED job.
        s2 = run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))

        assert s2.resumed is True
        # Provider must NOT be called again
        assert len(actor.calls) == calls_before
        # Dataset must not be modified (byte-identical records, same count)
        dataset_after = read_lines(ctx.dataset_path)
        assert dataset_after == dataset_before
        assert len(dataset_after) == 100
        # Checkpoint must not be modified either (no re-commit on this path)
        assert Path(ctx.checkpoint_path).read_text(encoding="utf-8") == ckpt_bytes_before
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
        test_max_items_hard_limit_oversized_page,
        test_resume_respects_remaining_cap,
        test_duplicate_heavy_page_does_not_consume_cap,
        test_checkpoint_matches_dataset_at_cap,
        test_crash_dataset_persisted_checkpoint_commit_fails,
        test_crash_checkpoint_temp_write_fails_previous_readable,
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
