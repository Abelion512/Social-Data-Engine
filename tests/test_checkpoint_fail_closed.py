#!/usr/bin/env python3
"""
Fail-closed checkpoint semantics (ENGINEERING_CONSTITUTION.md §3/§13).

Proves ONLY the contract that exists now:
 1. Corrupt checkpoint file      → CheckpointCorrupt raised by load()
 2. Resume over corrupt checkpoint → explicit terminal recovery failure
   (termination_reason="checkpoint_corrupt" → PERMANENT_FAILURE),
   NOT a silent fresh run: the actor receives ZERO fetch calls.
 3. Absent checkpoint + resume   → legitimate fresh start (unchanged).
 4. Valid checkpoint + resume    → unchanged behavior (no provider call on
   an already-terminated run).

Run: python tests/test_checkpoint_fail_closed.py
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
    RunContext,
    RunOptions,
    Outcome,
    CheckpointCorrupt,
)


class FakeActor(AcquisitionActor):
    def __init__(self, pages: List[PageResult]):
        self.provider_name = "fake"
        self.id_key = "item_id"
        self.pages = list(pages)
        self.calls: List[tuple] = []

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        self.calls.append((cursor, page_index))
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            return self.pages[idx]
        return PageResult(items=[], next_cursor=cursor, has_more=False)


def _items(prefix: str, start: int, count: int) -> List[dict]:
    return [{"item_id": f"{prefix}_{i}", "text": f"item {i}"}
            for i in range(start, start + count)]


def _ctx(tmpdir: str, job_id: str) -> RunContext:
    return RunContext.create(
        provider="fake",
        target_url="https://example.test/target/1",
        job_id=job_id,
        state_dir=str(Path(tmpdir) / "state"),
        data_dir=str(Path(tmpdir) / "data"),
    )


def _run(coro):
    return asyncio.run(coro)


def test_load_raises_on_corrupt_checkpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = _ctx(tmpdir, "job_corrupt")
        ckpt = CheckpointStore(ctx.checkpoint_path)
        ckpt.path.parent.mkdir(parents=True, exist_ok=True)
        ckpt.path.write_text("{not valid json!!", encoding="utf-8")
        try:
            ckpt.load()
            raised = False
        except CheckpointCorrupt:
            raised = True
        assert raised, "corrupt checkpoint must raise CheckpointCorrupt, not return None"


def test_resume_corrupt_checkpoint_fails_closed_not_fresh_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = _ctx(tmpdir, "job_fail_closed")
        ckpt = CheckpointStore(ctx.checkpoint_path)
        ckpt.path.parent.mkdir(parents=True, exist_ok=True)
        ckpt.path.write_text("{{{corrupt", encoding="utf-8")

        actor = FakeActor([
            PageResult(items=_items("x", 0, 10), next_cursor=10, has_more=False),
        ])
        summary = _run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))

        # Explicit terminal recovery failure — never a silent fresh run
        assert summary.termination_reason == "checkpoint_corrupt", summary
        assert summary.outcome == Outcome.PERMANENT_FAILURE, summary
        assert summary.resumed is False
        assert actor.calls == [], "no provider fetch may happen after corrupt resume"
        assert summary.items_written == 0
        assert summary.checkpoint_path == str(ctx.checkpoint_path)


def test_absent_checkpoint_resume_starts_fresh():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = _ctx(tmpdir, "job_fresh")
        actor = FakeActor([
            PageResult(items=_items("a", 0, 5), next_cursor=5, has_more=False),
        ])
        summary = _run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))
        assert summary.termination_reason == "has_more_false"
        assert summary.items_seen == 5
        assert len(actor.calls) == 1


def test_valid_checkpoint_resume_unchanged():
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = _ctx(tmpdir, "job_valid")
        actor = FakeActor([
            PageResult(items=_items("b", 0, 5), next_cursor=5, has_more=False),
        ])
        first = _run(AcquisitionRuntime().run(actor, ctx))
        assert first.termination_reason == "has_more_false"
        calls_after_first = len(actor.calls)

        # Resume a terminally-complete run: no new provider calls, count preserved
        second = _run(AcquisitionRuntime().run(actor, ctx, RunOptions(resume=True)))
        assert len(actor.calls) == calls_after_first
        assert second.termination_reason == "has_more_false"
        assert second.items_seen == 5
        # checkpoint on disk is valid JSON and loadable
        data = json.loads(Path(ctx.checkpoint_path).read_text())
        assert data["status"] == "done"


if __name__ == "__main__":
    tests = [
        test_load_raises_on_corrupt_checkpoint,
        test_resume_corrupt_checkpoint_fails_closed_not_fresh_run,
        test_absent_checkpoint_resume_starts_fresh,
        test_valid_checkpoint_resume_unchanged,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\nCheckpoint Fail-Closed Suite: {len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
