#!/usr/bin/env python3
"""
Unit tests for TikTok comment acquisition pagination, checkpoints, and resilience.

Tests pagination invariants:
1. Cursor advances
2. Cursor stalls (finite retries then terminates with explicit reason)
3. Empty page with has_more=True (transient empty page resilience)
4. Transient fetch failure + retry (recovers on success, terminates on max retries)
5. has_more=False explicit termination
6. Resume from checkpoint (resumes from cursor, page_index, items_seen)
7. Duplicate page does not corrupt progress or duplicate records
8. Max comments cap termination
9. Pipeline preservation: RawComment -> Canonical Observation -> Dedup
10. Simulated acquisition loop through 198+ comments to completion
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
    raw_from_api,
    raw_from_dom,
    write_jsonl,
)
from src.collector import _capture_pass, save_job, load_job, JOB_DIR
from src.schema.mapper import tiktok_to_canonical
from src.pipeline.dedup import dedup_all


def test_cursor_advances():
    """PaginationState advances cursor, increments page, and resets errors on valid new page."""
    state = PaginationState(cursor=0, page_index=0)
    assert state.cursor == 0
    assert state.page == 0
    assert state.has_more is True

    # Page 1: 100 comments returned, next cursor is 100
    comments_p1 = [{"cid": f"c_{i}", "text": f"cmt {i}"} for i in range(100)]
    state.process_page(comments=comments_p1, next_cursor=100, has_more=1)

    assert state.cursor == 100
    assert state.page == 1
    assert state.index == 1
    assert state.has_more is True
    assert state.consecutive_stalls == 0
    assert state.consecutive_empty == 0
    assert state.retry_count == 0
    assert state.termination_reason is None

    # Page 2: 98 comments returned, next cursor is 198
    comments_p2 = [{"cid": f"c_{i}", "text": f"cmt {i}"} for i in range(100, 198)]
    state.process_page(comments=comments_p2, next_cursor=198, has_more=True)

    assert state.cursor == 198
    assert state.page == 2
    assert state.has_more is True


def test_cursor_stalls():
    """Cursor stall (same cursor returned) is tolerated up to max_stalls before explicit termination."""
    state = PaginationState(cursor=198, page_index=2, max_stalls=3)

    # Repeat same cursor with items
    state.process_page(comments=[{"cid": "c_dup"}], next_cursor=198, has_more=1)
    assert state.consecutive_stalls == 1
    assert state.has_more is True

    state.process_page(comments=[{"cid": "c_dup"}], next_cursor=198, has_more=1)
    assert state.consecutive_stalls == 2
    assert state.has_more is True

    # Third stall exceeds max_stalls=3
    state.process_page(comments=[{"cid": "c_dup"}], next_cursor=198, has_more=1)
    assert state.has_more is False
    assert state.termination_reason == "cursor_stalled"


def test_empty_page_with_has_more_true():
    """Transient empty pages with has_more=True do not immediately terminate scraping."""
    state = PaginationState(cursor=198, page_index=2, max_empty_retries=5)

    # Empty page 1
    state.process_page(comments=[], next_cursor=198, has_more=1)
    assert state.has_more is True
    assert state.consecutive_empty == 1
    assert state.termination_reason is None

    # Empty page 2
    state.process_page(comments=[], next_cursor=198, has_more=1)
    assert state.has_more is True
    assert state.consecutive_empty == 2

    # Next page recovers and returns comments!
    state.process_page(comments=[{"cid": "c_new"}], next_cursor=250, has_more=1)
    assert state.has_more is True
    assert state.consecutive_empty == 0
    assert state.cursor == 250
    assert state.page == 3

    # Now verify that exceeding max_empty_retries terminates cleanly
    for _ in range(5):
        state.process_page(comments=[], next_cursor=250, has_more=1)

    assert state.has_more is False
    assert state.termination_reason == "max_empty_pages_exceeded"


def test_transient_fetch_failure_and_retry():
    """Fetch errors trigger retry count and recover upon subsequent success."""
    state = PaginationState(cursor=198, page_index=2, max_retries=3)

    state.record_error("network timeout")
    assert state.retry_count == 1
    assert state.has_more is True

    state.record_error("HTTP 503")
    assert state.retry_count == 2
    assert state.has_more is True

    # Recover on successful page
    state.process_page(comments=[{"cid": "c_ok"}], next_cursor=298, has_more=1)
    assert state.retry_count == 0
    assert state.has_more is True
    assert state.cursor == 298

    # Now verify exceeding max_retries
    state.record_error("err 1")
    state.record_error("err 2")
    state.record_error("err 3")
    assert state.has_more is False
    assert state.termination_reason == "max_retries_exceeded"


def test_has_more_false_termination():
    """Explicit has_more=0/False terminates pagination with 'has_more_false'."""
    state = PaginationState(cursor=198, page_index=2)
    state.process_page(comments=[{"cid": "c_last"}], next_cursor=250, has_more=0)

    assert state.has_more is False
    assert state.termination_reason == "has_more_false"
    assert state.cursor == 250


def test_resume_from_checkpoint():
    """State serialized to dict and reloaded preserves cursor, page_index, and items_seen."""
    state1 = PaginationState(
        cursor=198,
        page_index=2,
        items_seen=198,
        retry_count=0,
        has_more=True,
    )
    d = state1.to_dict()

    assert d["cursor"] == 198
    assert d["page_index"] == 2
    assert d["page"] == 2
    assert d["index"] == 2
    assert d["items_seen"] == 198
    assert d["has_more"] is True

    state2 = PaginationState.from_dict(d)
    assert state2.cursor == 198
    assert state2.page_index == 2
    assert state2.page == 2
    assert state2.items_seen == 198
    assert state2.has_more is True

    # Next page fetch continues from cursor 198 to 298
    state2.process_page(comments=[{"cid": "c_next"}], next_cursor=298, has_more=1)
    assert state2.cursor == 298
    assert state2.page == 3


def test_job_save_and_load():
    """Job checkpoint persistence with pagination sub-state."""
    video_id = "test_vid_9999"
    job_data = {
        "job_id": f"job_{video_id}",
        "video_id": video_id,
        "cursor": 198,
        "comments_seen": 198,
        "comments_written": 198,
        "status": "running",
        "pagination": PaginationState(cursor=198, page_index=2, items_seen=198).to_dict(),
    }
    save_job(video_id, job_data)
    loaded = load_job(video_id)

    assert loaded is not None
    assert loaded["video_id"] == video_id
    assert loaded["pagination"]["cursor"] == 198
    assert loaded["pagination"]["page_index"] == 2

    # Cleanup test job file
    p = JOB_DIR / f"{video_id}.json"
    if p.exists():
        p.unlink()


def test_duplicate_page_dedup():
    """Duplicate comments across pages do not corrupt items or count."""
    seen_ids = set()
    all_raw = []
    video_ctx = {"video_id": "123", "video_url": "https://tiktok.com/@u/video/123"}

    # Page 1: c1, c2
    batch1 = [{"cid": "c1", "text": "hello"}, {"cid": "c2", "text": "world"}]
    for item in batch1:
        cid = item["cid"]
        if cid not in seen_ids:
            seen_ids.add(cid)
            all_raw.append(raw_from_api(item, video_ctx))

    assert len(all_raw) == 2
    assert len(seen_ids) == 2

    # Page 2: repeats c2, adds c3
    batch2 = [{"cid": "c2", "text": "world"}, {"cid": "c3", "text": "extra"}]
    for item in batch2:
        cid = item["cid"]
        if cid not in seen_ids:
            seen_ids.add(cid)
            all_raw.append(raw_from_api(item, video_ctx))

    assert len(all_raw) == 3
    assert len(seen_ids) == 3


def test_max_comments_cap():
    """Reaching max_comments cap terminates pagination with 'max_comments_reached'."""
    state = PaginationState(cursor=198, page_index=2, items_seen=198)
    assert state.check_cap(max_comments=200) is False
    assert state.has_more is True

    state.record_items(200)
    assert state.check_cap(max_comments=200) is True
    assert state.has_more is False
    assert state.termination_reason == "max_comments_reached"


def test_raw_canonical_dedup_pipeline():
    """RawComment -> Canonical Observation -> Dedup pipeline preserves attributes and relationships."""
    video_ctx = {"video_id": "7620779574355758356", "video_url": "https://tiktok.com/@u/video/7620779574355758356", "caption": "test caption"}
    raw1 = raw_from_api({"cid": "c100", "text": "Great video!", "user": {"id": "u1", "unique_id": "alice"}}, video_ctx)
    raw2 = raw_from_api({"cid": "c101", "text": "Great video!", "user": {"id": "u2", "unique_id": "bob"}}, video_ctx)
    raw3 = raw_from_api({"cid": "c102", "text": "Different comment", "user": {"id": "u3", "unique_id": "charlie"}}, video_ctx)

    obs1 = tiktok_to_canonical(raw1)
    obs2 = tiktok_to_canonical(raw2)
    obs3 = tiktok_to_canonical(raw3)

    assert obs1.observation_id == "tiktok:c100"
    assert obs1.source == "tiktok"
    assert obs1.content.text_raw == "Great video!"
    assert obs1.content.text_normalized == "great video!"

    # Dedup exact: obs1 and obs2 have exact same text -> obs2 dropped
    deduped = dedup_all([obs1, obs2, obs3])
    assert len(deduped) == 2
    deduped_ids = [o.observation_id for o in deduped]
    assert "tiktok:c100" in deduped_ids
    assert "tiktok:c102" in deduped_ids


def test_async_capture_pass_simulation():
    """Simulate _capture_pass advancing past 198 comments through multiple pages without false stalls."""
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "test_raw.jsonl"
            video_id = "7620779574355758356"
            video_ctx = {"video_id": video_id, "video_url": f"https://www.tiktok.com/@u/video/{video_id}"}
            job_state = {"job_id": f"job_{video_id}", "video_id": video_id}
            all_raw = []
            seen_ids = set()
            captured_pages = []
            pagination_state = PaginationState()

            # Mock page evaluate (DOM returns 1 comment initially)
            dom_call_count = [0]
            def mock_evaluate(js, *args):
                dom_call_count[0] += 1
                if "comment-level" in js:
                    return json.dumps([{"comment_id": "dom_1", "raw": "dom comment 1"}])
                return "{}"

            page = MagicMock()
            page.evaluate = AsyncMock(side_effect=mock_evaluate)
            page.locator = MagicMock()
            page.locator.return_value.count = AsyncMock(return_value=0)
            page.locator.return_value.all = AsyncMock(return_value=[])

            # Pre-populate simulated API pages:
            # Iteration 1: returns 100 comments, cursor=100, has_more=1
            # Iteration 2: returns 98 comments (total 198), cursor=198, has_more=1
            # Iteration 3: returns empty comments, cursor=198, has_more=1 (transient empty page)
            # Iteration 4: returns 50 comments (total 248), cursor=248, has_more=0 (completion!)
            api_pages = [
                {"comments": [{"cid": f"api_{i}", "text": f"comment {i}"} for i in range(100)], "cursor": 100, "has_more": 1},
                {"comments": [{"cid": f"api_{i}", "text": f"comment {i}"} for i in range(100, 198)], "cursor": 198, "has_more": 1},
                {"comments": [], "cursor": 198, "has_more": 1},
                {"comments": [{"cid": f"api_{i}", "text": f"comment {i}"} for i in range(198, 248)], "cursor": 248, "has_more": 0},
            ]
            api_call_idx = [0]

            async def mock_fetch_api(p, vid, cur):
                idx = api_call_idx[0]
                api_call_idx[0] += 1
                if idx < len(api_pages):
                    return api_pages[idx]
                return {"comments": [], "cursor": cur, "has_more": 0}

            # Monkey-patch fetch_comments_api in src.collector
            import src.collector
            orig_fetch = src.collector.fetch_comments_api
            orig_delay = src.collector.ahuman_delay
            orig_scroll = src.collector.human_scroll
            src.collector.fetch_comments_api = mock_fetch_api
            src.collector.ahuman_delay = AsyncMock(return_value=None)
            src.collector.human_scroll = AsyncMock(return_value=None)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                try:
                    new_count = await _capture_pass(
                        page=page,
                        video_ctx=video_ctx,
                        captured_pages=captured_pages,
                        all_raw=all_raw,
                        seen_ids=seen_ids,
                        out_path=out_path,
                        video_id=video_id,
                        job_state=job_state,
                        pagination_state=pagination_state,
                        max_scrolls=10,
                        max_comments=2000,
                    )

                    # Total unique raw comments collected: 1 DOM + 248 API = 249 comments!
                    # Successfully surpassed 198 comments!
                    assert len(all_raw) == 249, f"Expected 249 comments, got {len(all_raw)}"
                    assert pagination_state.has_more is False
                    assert pagination_state.termination_reason == "has_more_false"
                    assert pagination_state.cursor == 248
                    assert out_path.exists()
                    # Verify JSONL lines written
                    lines = [l for l in out_path.read_text().splitlines() if l.strip()]
                    assert len(lines) == 249
                finally:
                    src.collector.fetch_comments_api = orig_fetch
                    src.collector.ahuman_delay = orig_delay
                    src.collector.human_scroll = orig_scroll

    asyncio.run(_run())


if __name__ == "__main__":
    tests = [
        test_cursor_advances,
        test_cursor_stalls,
        test_empty_page_with_has_more_true,
        test_transient_fetch_failure_and_retry,
        test_has_more_false_termination,
        test_resume_from_checkpoint,
        test_job_save_and_load,
        test_duplicate_page_dedup,
        test_max_comments_cap,
        test_raw_canonical_dedup_pipeline,
        test_async_capture_pass_simulation,
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

    print(f"\nSummary: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
