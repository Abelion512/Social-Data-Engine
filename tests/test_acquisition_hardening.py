#!/usr/bin/env python3
"""
Comprehensive Failure-Injection & Hardening Test Suite for Acquisition Layer.

Tests:
1. Incremental persistence (verifying atomic append and fsync on raw events)
2. Checkpoint ordering (checkpoint only written AFTER successful raw persistence)
3. Crash after page N recovery (resumes from cursor N, zero lost records)
4. Crash during page N recovery (replaying page N deduplicates raw & canonical records)
5. Idempotency guarantees (replaying identical pages produces zero duplicate canonicals)
6. Failure discrimination:
   - fetch failure
   - parse failure
   - empty page (transient vs terminal)
   - pagination stall
   - auth/block challenge
   - normal completion
7. Structured acquisition metrics and telemetry accuracy
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tiktok_schema import (
    Author,
    RawComment,
    PaginationState,
    AcquisitionMetrics,
    TerminationReason,
    raw_from_api,
    raw_from_dom,
    write_jsonl,
    append_raw_records,
)
from src.collector import (
    _capture_pass,
    save_job,
    load_job,
    fetch_comments_api,
    JOB_DIR,
)
from src.schema.mapper import tiktok_to_canonical
from src.pipeline.dedup import dedup_all


def test_incremental_raw_persistence():
    """Verify that records are persisted incrementally per batch without rewriting previous records."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_file = Path(tmpdir) / "raw.jsonl"
        seen_ids = set()

        batch1 = [
            {"comment_id": "c1", "text_raw": "first"},
            {"comment_id": "c2", "text_raw": "second"},
        ]
        written1 = append_raw_records(str(raw_file), batch1, seen_ids=seen_ids)
        assert written1 == 2
        assert seen_ids == {"c1", "c2"}
        assert raw_file.exists()

        lines1 = [json.loads(line) for line in raw_file.read_text().splitlines() if line.strip()]
        assert len(lines1) == 2
        assert [r["comment_id"] for r in lines1] == ["c1", "c2"]

        # Batch 2: adds c3 and tries to re-add c2
        batch2 = [
            {"comment_id": "c2", "text_raw": "second"},
            {"comment_id": "c3", "text_raw": "third"},
        ]
        written2 = append_raw_records(str(raw_file), batch2, seen_ids=seen_ids)
        assert written2 == 1  # Only c3 is written!
        assert seen_ids == {"c1", "c2", "c3"}

        lines2 = [json.loads(line) for line in raw_file.read_text().splitlines() if line.strip()]
        assert len(lines2) == 3
        assert [r["comment_id"] for r in lines2] == ["c1", "c2", "c3"]


def test_checkpoint_persisted_only_after_successful_page_write():
    """Verify that a disk write failure prevents the checkpoint from advancing."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "raw_fail_test.jsonl"
            video_id = "test_chk_order"
            video_ctx = {"video_id": video_id, "video_url": f"https://tiktok.com/@u/video/{video_id}"}
            job_state = {"job_id": f"job_{video_id}", "video_id": video_id, "cursor": 0, "page_index": 0}
            all_raw = []
            seen_ids = set()
            captured_pages = []
            pagination_state = PaginationState(cursor=0, page_index=0)

            page = MagicMock()
            page.evaluate = AsyncMock(return_value="[]")
            page.locator = MagicMock()
            page.locator.return_value.count = AsyncMock(return_value=0)
            page.locator.return_value.all = AsyncMock(return_value=[])

            # Simulated API page: returns comments with next cursor 100
            api_resp = {
                "comments": [{"cid": "c1", "text": "hello"}],
                "cursor": 100,
                "has_more": 1,
            }

            import src.collector
            orig_fetch = src.collector.fetch_comments_api
            orig_append = src.collector.append_raw_records
            orig_save_job = src.collector.save_job

            save_job_called = [False]
            def mock_save_job(vid, data):
                save_job_called[0] = True

            def mock_failing_append(path, records, seen_ids=None):
                raise OSError("Disk write I/O error - disk full")

            src.collector.fetch_comments_api = AsyncMock(return_value=api_resp)
            src.collector.append_raw_records = mock_failing_append
            src.collector.save_job = mock_save_job
            src.collector.ahuman_delay = AsyncMock(return_value=None)
            src.collector.human_scroll = AsyncMock(return_value=None)

            try:
                try:
                    await _capture_pass(
                        page=page,
                        video_ctx=video_ctx,
                        captured_pages=captured_pages,
                        all_raw=all_raw,
                        seen_ids=seen_ids,
                        out_path=out_path,
                        video_id=video_id,
                        job_state=job_state,
                        pagination_state=pagination_state,
                        max_scrolls=1,
                    )
                except OSError:
                    pass

                # Checkpoint must NOT have been saved
                assert save_job_called[0] is False, "Checkpoint was saved despite raw disk write failure!"
            finally:
                src.collector.fetch_comments_api = orig_fetch
                src.collector.append_raw_records = orig_append
                src.collector.save_job = orig_save_job

    asyncio.run(_run())


def test_crash_after_page_n_resumes_correctly():
    """Verify that after page N is written and checkpointed, crash recovery resumes from cursor N."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "raw.jsonl"
            video_id = "test_crash_p2"
            video_ctx = {"video_id": video_id, "video_url": f"https://tiktok.com/@u/video/{video_id}"}

            # Pre-populate checkpoint as if page 1 (items 0..99, cursor=100) succeeded
            p1_comments = [{"comment_id": f"c_{i}", "text_raw": f"text {i}", "video_id": video_id} for i in range(100)]
            append_raw_records(str(out_path), p1_comments)

            initial_state = PaginationState(
                cursor=100,
                page_index=1,
                items_seen=100,
                metrics=AcquisitionMetrics(pages_attempted=1, pages_succeeded=1, items_collected=100, items_unique=100),
            )
            job_state = {
                "job_id": f"job_{video_id}",
                "video_id": video_id,
                "cursor": 100,
                "page_index": 1,
                "comments_seen": 100,
                "comments_written": 100,
                "status": "running",
                "pagination": initial_state.to_dict(),
                "metrics": initial_state.metrics.to_dict(),
            }
            save_job(video_id, job_state)

            # Resume session
            loaded_job = load_job(video_id)
            assert loaded_job is not None
            resumed_pag = PaginationState.from_dict(loaded_job["pagination"])
            assert resumed_pag.cursor == 100
            assert resumed_pag.page_index == 1

            all_raw = []
            seen_ids = set()
            # Read existing comments from raw
            for line in out_path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    seen_ids.add(d["comment_id"])
                    all_raw.append(raw_from_dom({"comment_id": d["comment_id"], "raw": d["text_raw"]}, video_ctx))

            assert len(seen_ids) == 100
            assert len(all_raw) == 100

            page = MagicMock()
            page.evaluate = AsyncMock(return_value="[]")
            page.locator = MagicMock()
            page.locator.return_value.count = AsyncMock(return_value=0)
            page.locator.return_value.all = AsyncMock(return_value=[])

            # Page 2 returns items 100..149 with next_cursor=150, has_more=0
            p2_response = {
                "comments": [{"cid": f"c_{i}", "text": f"text {i}"} for i in range(100, 150)],
                "cursor": 150,
                "has_more": 0,
            }

            import src.collector
            orig_fetch = src.collector.fetch_comments_api
            src.collector.fetch_comments_api = AsyncMock(return_value=p2_response)
            src.collector.ahuman_delay = AsyncMock(return_value=None)
            src.collector.human_scroll = AsyncMock(return_value=None)

            try:
                await _capture_pass(
                    page=page,
                    video_ctx=video_ctx,
                    captured_pages=[],
                    all_raw=all_raw,
                    seen_ids=seen_ids,
                    out_path=out_path,
                    video_id=video_id,
                    job_state=loaded_job,
                    pagination_state=resumed_pag,
                    max_scrolls=1,
                )

                assert len(all_raw) == 150
                assert len(seen_ids) == 150
                assert resumed_pag.cursor == 150
                assert resumed_pag.has_more is False
                assert resumed_pag.termination_reason == "has_more_false"

                # Verify file on disk
                lines = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
                assert len(lines) == 150
                cids = [l["comment_id"] for l in lines]
                assert len(set(cids)) == 150  # Exactly 150 unique comments, zero duplicates!
            finally:
                src.collector.fetch_comments_api = orig_fetch
                p = JOB_DIR / f"{video_id}.json"
                if p.exists():
                    p.unlink()

    asyncio.run(_run())


def test_crash_during_page_n_replay_deduplication():
    """Verify that replaying a partially written page deduplicates without duplicating canonical records."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "raw.jsonl"
            video_id = "test_replay_dedup"
            video_ctx = {"video_id": video_id, "video_url": f"https://tiktok.com/@u/video/{video_id}"}

            # Scenario: Checkpoint was at page 0 (cursor=0).
            # But raw file contains partial write of c1, c2 from interrupted page 1.
            partial_comments = [
                {"comment_id": "c1", "text_raw": "hello", "video_id": video_id},
                {"comment_id": "c2", "text_raw": "world", "video_id": video_id},
            ]
            append_raw_records(str(out_path), partial_comments)

            # On resume, load existing comments into seen_ids
            seen_ids = set()
            all_raw = []
            for line in out_path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    seen_ids.add(d["comment_id"])
                    all_raw.append(raw_from_api({"cid": d["comment_id"], "text": d["text_raw"]}, video_ctx))

            assert len(seen_ids) == 2

            # Now Page 1 is re-fetched from cursor 0, returning c1, c2, AND c3
            page1_replay = {
                "comments": [
                    {"cid": "c1", "text": "hello"},
                    {"cid": "c2", "text": "world"},
                    {"cid": "c3", "text": "new item"},
                ],
                "cursor": 100,
                "has_more": 0,
            }

            page = MagicMock()
            page.evaluate = AsyncMock(return_value="[]")
            page.locator = MagicMock()
            page.locator.return_value.count = AsyncMock(return_value=0)
            page.locator.return_value.all = AsyncMock(return_value=[])

            import src.collector
            orig_fetch = src.collector.fetch_comments_api
            src.collector.fetch_comments_api = AsyncMock(return_value=page1_replay)
            src.collector.ahuman_delay = AsyncMock(return_value=None)
            src.collector.human_scroll = AsyncMock(return_value=None)

            pag_state = PaginationState(cursor=0, page_index=0)
            job_state = {"job_id": f"job_{video_id}", "video_id": video_id}

            try:
                await _capture_pass(
                    page=page,
                    video_ctx=video_ctx,
                    captured_pages=[],
                    all_raw=all_raw,
                    seen_ids=seen_ids,
                    out_path=out_path,
                    video_id=video_id,
                    job_state=job_state,
                    pagination_state=pag_state,
                    max_scrolls=1,
                )

                # Exactly 3 comments in all_raw
                assert len(all_raw) == 3
                assert len(seen_ids) == 3

                # Exactly 3 lines in raw JSONL
                lines = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
                assert len(lines) == 3
                assert [l["comment_id"] for l in lines] == ["c1", "c2", "c3"]

                # Canonical conversion + dedup produces exactly 3 observations
                canonical_obs = [tiktok_to_canonical(r) for r in all_raw]
                deduped = dedup_all(canonical_obs)
                assert len(deduped) == 3
                assert {o.observation_id for o in deduped} == {"tiktok:c1", "tiktok:c2", "tiktok:c3"}
            finally:
                src.collector.fetch_comments_api = orig_fetch
                p = JOB_DIR / f"{video_id}.json"
                if p.exists():
                    p.unlink()

    asyncio.run(_run())


def test_failure_discrimination_fetch_failure():
    """Verify fetch failure classification and termination after max retries."""
    state = PaginationState(cursor=100, page_index=1, max_retries=3)
    state.process_page(error="Connection reset by peer", error_type="fetch_failure")
    assert state.metrics.fetch_errors == 1
    assert state.retry_count == 1
    assert state.has_more is True

    state.process_page(error="Gateway Timeout", error_type="fetch_failure")
    assert state.metrics.fetch_errors == 2
    assert state.retry_count == 2
    assert state.has_more is True

    state.process_page(error="Network unreachable", error_type="fetch_failure")
    assert state.metrics.fetch_errors == 3
    assert state.retry_count == 3
    assert state.has_more is False
    assert state.termination_reason == "max_retries_exceeded"


def test_failure_discrimination_parse_failure():
    """Verify parse/JSON decode failure classification and termination."""
    state = PaginationState(cursor=100, page_index=1, max_parse_retries=3)
    state.process_page(error="Invalid JSON response from server", error_type="parse")
    assert state.metrics.parse_errors == 1
    assert state.consecutive_parse_errors == 1
    assert state.has_more is True

    state.process_page(error="Unexpected non-dict payload", error_type="parse_failure")
    assert state.metrics.parse_errors == 2
    assert state.consecutive_parse_errors == 2
    assert state.has_more is True

    state.process_page(error="Malformed JSON token", error_type="parse")
    assert state.metrics.parse_errors == 3
    assert state.consecutive_parse_errors == 3
    assert state.has_more is False
    assert state.termination_reason == "parse_failure"


def test_failure_discrimination_empty_page():
    """Verify transient empty page tolerance and termination when exceeding max empty retries."""
    state = PaginationState(cursor=100, page_index=1, max_empty_retries=3)
    # Transient empty page 1
    state.process_page(comments=[], next_cursor=100, has_more=1)
    assert state.metrics.empty_pages == 1
    assert state.consecutive_empty == 1
    assert state.has_more is True

    # Transient empty page 2
    state.process_page(comments=[], next_cursor=100, has_more=1)
    assert state.metrics.empty_pages == 2
    assert state.consecutive_empty == 2
    assert state.has_more is True

    # Exceeding max empty retries
    state.process_page(comments=[], next_cursor=100, has_more=1)
    assert state.metrics.empty_pages == 3
    assert state.consecutive_empty == 3
    assert state.has_more is False
    assert state.termination_reason == "max_empty_pages_exceeded"


def test_failure_discrimination_pagination_stall():
    """Verify pagination stall (unchanged cursor with items) termination."""
    state = PaginationState(cursor=100, page_index=1, max_stalls=2)
    state.process_page(comments=[{"cid": "dup"}], next_cursor=100, has_more=1)
    assert state.metrics.stalls == 1
    assert state.consecutive_stalls == 1
    assert state.has_more is True

    state.process_page(comments=[{"cid": "dup"}], next_cursor=100, has_more=1)
    assert state.metrics.stalls == 2
    assert state.consecutive_stalls == 2
    assert state.has_more is False
    assert state.termination_reason == "cursor_stalled"


def test_failure_discrimination_auth_block():
    """Verify authentication / CAPTCHA / anti-bot block classification and immediate termination."""
    state = PaginationState(cursor=100, page_index=1)
    state.process_page(error="TikTok verification slider challenge", error_type="auth")
    assert state.metrics.auth_blocks == 1
    assert state.has_more is False
    assert state.termination_reason == "auth_blocked"


def test_failure_discrimination_normal_completion():
    """Verify normal completion when has_more=False."""
    state = PaginationState(cursor=100, page_index=1)
    state.process_page(comments=[{"cid": "c_last"}], next_cursor=150, has_more=0)
    assert state.has_more is False
    assert state.termination_reason == "has_more_false"
    assert state.metrics.pages_succeeded == 1
    assert state.metrics.items_unique == 1
    assert state.metrics.last_success_at is not None


def test_structured_acquisition_metrics_telemetry():
    """Verify structured acquisition metrics counters and serialized dict correctness."""
    metrics = AcquisitionMetrics(
        pages_attempted=5,
        pages_succeeded=4,
        items_collected=400,
        items_unique=380,
        items_deduplicated=20,
        fetch_errors=1,
        parse_errors=0,
        empty_pages=1,
        stalls=0,
        auth_blocks=0,
        duration_seconds=12.5,
    )
    d = metrics.to_dict()
    assert d["pages_attempted"] == 5
    assert d["pages_succeeded"] == 4
    assert d["items_collected"] == 400
    assert d["items_unique"] == 380
    assert d["items_deduplicated"] == 20
    assert d["fetch_errors"] == 1
    assert d["duration_seconds"] == 12.5

    reloaded = AcquisitionMetrics.from_dict(d)
    assert reloaded.pages_attempted == 5
    assert reloaded.items_deduplicated == 20


if __name__ == "__main__":
    tests = [
        test_incremental_raw_persistence,
        test_checkpoint_persisted_only_after_successful_page_write,
        test_crash_after_page_n_resumes_correctly,
        test_crash_during_page_n_replay_deduplication,
        test_failure_discrimination_fetch_failure,
        test_failure_discrimination_parse_failure,
        test_failure_discrimination_empty_page,
        test_failure_discrimination_pagination_stall,
        test_failure_discrimination_auth_block,
        test_failure_discrimination_normal_completion,
        test_structured_acquisition_metrics_telemetry,
    ]
    passed = 0
    failed = 0
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

    print(f"\nHardening Suite Summary: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
