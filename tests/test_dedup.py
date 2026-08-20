#!/usr/bin/env python3
"""Tests for Multi-Tier Deduplication pipeline."""

from src.pipeline.dedup import dedup_exact, dedup_normalized, dedup_near_duplicate, dedup_all
from src.schema.canonical import Observation


def test_dedup_exact():
    """Tier 1: exact text dedup — hash text mentah, skip duplikat persis."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content={"text_raw": "hello world", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content={"text_raw": "hello world", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content={"text_raw": "goodbye world", "text_normalized": "goodbye world"},
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
            content={"text_raw": "Hello   World", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content={"text_raw": "hello world", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content={"text_raw": "HELLO WORLD", "text_normalized": "hello world"},
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
            content={"text_raw": "hello world this is test", "text_normalized": "hello world this is test"},
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content={"text_raw": "hello world this is test", "text_normalized": "hello world this is test"},
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content={"text_raw": "hello world this is different", "text_normalized": "hello world this is different"},
        ),
    ]
    result = dedup_near_duplicate(records, threshold=0.85)
    # test-1 and test-2 should be near-duplicate (identical), only 1 kept
    # test-3 should be kept as different
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    ids = [r.observation_id for r in result]
    assert "test-3" in ids


def test_dedup_all():
    """Apply semua tiga tingkat — urutan penting: exact → normalized → near."""
    records = [
        Observation(
            observation_id="test-1",
            source="tiktok",
            content={"text_raw": "hello world", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-2",
            source="tiktok",
            content={"text_raw": "hello world", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-3",
            source="tiktok",
            content={"text_raw": "Hello   World", "text_normalized": "hello world"},
        ),
        Observation(
            observation_id="test-4",
            source="tiktok",
            content={"text_raw": "goodbye world", "text_normalized": "goodbye world"},
        ),
    ]
    result = dedup_all(records)
    # test-1 (exact dup of test-2) removed by exact
    # test-2 removed by exact (dup of test-1)
    # test-3 removed by normalized (same as test-1 after fold)
    # test-4 kept (unique)
    assert len(result) == 1, f"Expected 1 record, got {len(result)}"
    assert result[0].observation_id == "test-4"