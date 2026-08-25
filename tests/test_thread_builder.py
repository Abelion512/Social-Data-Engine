#!/usr/bin/env python3
"""Tests for thread_builder — conversation-unit tree reconstruction."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.thread_builder import (
    ThreadNode, build_threads, flatten_threads, count_threads,
)
from src.schema.canonical import Observation, Content


def _make_obs(obs_id, parent="", text="hello"):
    meta = {"parent_comment_id": parent} if parent else {}
    return Observation(
        observation_id=obs_id,
        source="tiktok",
        content=Content(text_raw=text, text_normalized=text.lower(), metadata=meta),
    )


def test_build_threads_flat_list():
    recs = [_make_obs(f"r{i}", parent="", text=f"msg{i}") for i in range(3)]
    roots = build_threads(recs)
    assert len(roots) == 3
    assert count_threads(roots) == {"roots": 3, "nodes": 3, "replies": 0}


def test_build_threads_nested():
    recs = [
        _make_obs("parent", parent="", text="top level"),
        _make_obs("child1", parent="parent", text="reply 1"),
        _make_obs("child2", parent="parent", text="reply 2"),
    ]
    roots = build_threads(recs)
    assert len(roots) == 1
    assert len(roots[0].children) == 2
    assert {c.observation_id for c in roots[0].children} == {"child1", "child2"}
    assert count_threads(roots) == {"roots": 1, "nodes": 3, "replies": 2}


def test_build_threads_orphan():
    recs = [
        _make_obs("orphan_reply", parent="missing_parent", text="no parent here"),
        _make_obs("solo", parent="", text="standalone"),
    ]
    roots = build_threads(recs)
    assert len(roots) == 2
    flat = flatten_threads(roots)
    assert len(flat) == 2
    assert {r.observation_id for r in flat} == {"orphan_reply", "solo"}


def test_build_threads_self_reference():
    recs = [_make_obs("self_ref", parent="self_ref", text="i am my own parent")]
    roots = build_threads(recs)
    assert len(roots) == 1
    assert len(roots[0].children) == 0
    assert roots[0].observation_id == "self_ref"


def test_build_threads_preserves_all():
    recs = [
        _make_obs("p1", parent="", text="a"),
        _make_obs("c1", parent="p1", text="b"),
        _make_obs("c2", parent="p1", text="c"),
        _make_obs("p2", parent="", text="d"),
        _make_obs("c3", parent="p2", text="e"),
        _make_obs("orphan", parent="ghost", text="f"),
    ]
    roots = build_threads(recs)
    flat_ids = {r.observation_id for r in flatten_threads(roots)}
    expected_ids = {r.observation_id for r in recs}
    assert flat_ids == expected_ids
    assert count_threads(roots) == {"roots": 3, "nodes": 6, "replies": 3}


def test_build_threads_empty():
    roots = build_threads([])
    assert roots == []
    assert count_threads(roots) == {"roots": 0, "nodes": 0, "replies": 0}


def test_thread_node_observation_id_property():
    node = ThreadNode(observation=_make_obs("abc", parent="xyz"))
    assert node.observation_id == "abc"


if __name__ == "__main__":
    test_build_threads_flat_list()
    test_build_threads_nested()
    test_build_threads_orphan()
    test_build_threads_self_reference()
    test_build_threads_preserves_all()
    test_build_threads_empty()
    test_thread_node_observation_id_property()
    print("test_thread_builder.py: 7 passed ✅")
