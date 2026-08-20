#!/usr/bin/env python3
"""Self-contained test runner for dedup + quality — no pytest needed."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_dedup_exact():
    """Tier 1: exact text dedup — hash text mentah, skip duplikat persis."""
    from src.pipeline.dedup import dedup_exact
    from src.schema.canonical import Observation, Content

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
    print("PASS: test_dedup_exact")


def test_dedup_normalized():
    """Tier 2: normalized dedup — case/whitespace/unicode fold."""
    from src.pipeline.dedup import dedup_normalized
    from src.schema.canonical import Observation, Content

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
    print("PASS: test_dedup_normalized")


def test_dedup_near_duplicate():
    """Tier 3: near-duplicate dedup — Jaccard similarity pada bigram."""
    from src.pipeline.dedup import dedup_near_duplicate
    from src.schema.canonical import Observation, Content

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
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    ids = [r.observation_id for r in result]
    assert "test-3" in ids
    print("PASS: test_dedup_near_duplicate")


def test_dedup_all():
    """Apply semua tiga tingkat — urutan penting: exact → normalized → near."""
    from src.pipeline.dedup import dedup_all
    from src.schema.canonical import Observation, Content

    # Dedup exact menghilangkan "hello world" duplikat
    # Dedup normalized menghilangkan "Hello   World" karena normal jadi "hello world"
    # "goodbye world" beda, jadi 2 record yang selamat
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
    assert len(result) == 2, f"Expected 2 records, got {len(result)}"
    ids = [r.observation_id for r in result]
    assert "test-4" in ids  # "goodbye world" selamat
    print("PASS: test_dedup_all")


def test_empty_text():
    from src.pipeline.quality import compute_quality_score, passes_gate, passes_llm_gate
    from src.schema.canonical import Observation, Content, Provenance

    obs = Observation(
        observation_id="test-empty",
        source="tiktok",
        content=Content(text_raw=""),
        provenance=Provenance(collector_version="test", pipeline_version="test", source="tiktok")
    )
    score = compute_quality_score(obs)
    assert score["curated_score"] == 0.0
    assert score["spam_probability"] == 1.0
    assert not passes_gate(obs)
    assert not passes_llm_gate(obs)
    print("PASS: test_empty_text")


def test_high_quality():
    from src.pipeline.quality import compute_quality_score, passes_gate, passes_llm_gate
    from src.schema.canonical import Observation, Content, Provenance

    obs = Observation(
        observation_id="test-high",
        source="tiktok",
        content=Content(text_raw="This is a meaningful comment with insights."),
        provenance=Provenance(collector_version="test", pipeline_version="test", source="tiktok")
    )
    score = compute_quality_score(obs)
    assert score["quality"] > 0.5
    assert passes_gate(obs)
    assert passes_llm_gate(obs)
    print("PASS: test_high_quality")


def test_spam_threshold():
    from src.pipeline.quality import compute_quality_score, passes_gate, passes_llm_gate
    from src.schema.canonical import Observation, Content, Provenance

    obs = Observation(
        observation_id="test-spam",
        source="tiktok",
        content=Content(text_raw="https://spam.link/1 https://spam.link/2 https://spam.link/3"),
        provenance=Provenance(collector_version="test", pipeline_version="test", source="tiktok")
    )
    score = compute_quality_score(obs)
    assert score["spam_probability"] > 0.5
    assert not passes_gate(obs)
    assert not passes_llm_gate(obs)
    print("PASS: test_spam_threshold")


def test_gating_threshold():
    from src.pipeline.quality import passes_llm_gate
    from src.schema.canonical import Observation, Content, Provenance

    obs = Observation(
        observation_id="test-gate",
        source="tiktok",
        content=Content(text_raw="Short but relevant comment"),
        provenance=Provenance(collector_version="test", pipeline_version="test", source="tiktok")
    )
    assert passes_llm_gate(obs)
    print("PASS: test_gating_threshold")


if __name__ == "__main__":
    tests = [
        test_dedup_exact,
        test_dedup_normalized,
        test_dedup_near_duplicate,
        test_dedup_all,
        test_empty_text,
        test_high_quality,
        test_spam_threshold,
        test_gating_threshold,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
