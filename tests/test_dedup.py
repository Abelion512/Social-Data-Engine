#!/usr/bin/env python3
"""Tests for Multi-Tier Deduplication pipeline.

NOTE: `Observation.content` is a `Content` dataclass (see src/schema/canonical.py),
NOT a plain dict. The dedup tiers access `r.content.text_raw` (attribute access),
so test fixtures must construct `Content(...)` objects — exactly as the
self-contained runner `tests/run_dedup_quality_tests.py` does (these two suites
must agree).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.dedup import dedup_exact, dedup_normalized, dedup_near_duplicate, dedup_all
from src.schema.canonical import Observation, Content


def test_dedup_exact():
    """Tier 1: exact text dedup — hash text mentah, skip duplikat persis."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content=Content(text_raw="hello world", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content=Content(text_raw="hello world", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content=Content(text_raw="goodbye world", text_normalized="goodbye world"),
        ),
    ]
    result = dedup_exact(records)
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    ids = [r.observation_id for r in result]
    assert "test-1" in ids
    assert "test-3" in ids
    assert "test-2" not in ids


def test_dedup_normalized():
    """Tier 2: normalized dedup — case/whitespace/unicode fold."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content=Content(text_raw="Hello   World", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content=Content(text_raw="hello world", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content=Content(text_raw="HELLO WORLD", text_normalized="hello world"),
        ),
    ]
    result = dedup_normalized(records)
    assert len(result) == 1, f"Expected 1 record, got {len(result)}"
    assert result[0].observation_id == "test-1"


def test_dedup_near_duplicate():
    """Tier 3: near-duplicate dedup — Jaccard similarity pada bigram."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content=Content(text_raw="hello world this is test", text_normalized="hello world this is test"),
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content=Content(text_raw="hello world this is test", text_normalized="hello world this is test"),
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content=Content(text_raw="hello world this is different", text_normalized="hello world this is different"),
        ),
    ]
    result = dedup_near_duplicate(records, threshold=0.85)
    # test-1 and test-2 should be near-duplicate (identical), only 1 kept
    # test-3 should be kept as different
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    ids = [r.observation_id for r in result]
    assert "test-1" in ids
    assert "test-3" in ids


def test_dedup_all():
    """Apply semua tiga tingkat — urutan penting: exact → normalized → near.
    Per-parent (thread-aware) sehingga reply ke parent beda tidak diserap."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content=Content(text_raw="hello world", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content=Content(text_raw="hello world", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content=Content(text_raw="Hello   World", text_normalized="hello world"),
        ),
        Observation(
            observation_id="test-4",
            source="tiktok",
            content=Content(text_raw="goodbye world", text_normalized="goodbye world"),
        ),
    ]
    result = dedup_all(records)
    # dedup keeps the FIRST occurrence per (parent, text) key:
    #   - test-2 is exact-dup of test-1  → removed
    #   - test-3 folds to "hello world"  → normalized-dup of test-1 → removed
    #   - test-1 and test-4 survive (distinct text, Jaccard < 0.85)
    ids = [r.observation_id for r in result]
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    assert "test-1" in ids
    assert "test-4" in ids


if __name__ == "__main__":
    test_dedup_exact()
    test_dedup_normalized()
    test_dedup_near_duplicate()
    test_dedup_all()
    print("test_dedup.py: 4 passed ✅")
