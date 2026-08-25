#!/usr/bin/env python3
"""
Conversation-unit / thread builder — reconstruct reply trees from Observations.

Takes a flat list of Observation (each carries parent_comment_id in
content.metadata, mirroring dedup._parent) and produces conversation trees:
top-level comments as roots, replies nested under their parent.

Invariant: every observation appears exactly once; replies attach to an
existing parent or become roots (orphan-safe). Cycles are broken (self/loop
parent -> root) so the structure stays a valid tree.

ponytail: single-pass O(n) index + attach; TikTok replies are <=2 levels, so
no topological sort needed. Upgrade to DAG/forest if deeper nesting appears.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from src.schema.canonical import Observation


def _index_key(r: Observation) -> str:
    """
    Stable identifier for indexing.
    - Prefers observation_id if set (canonical pipeline path)
    - Falls back to comment_id from content.metadata (raw observations via from_dict)
    """
    oid = getattr(r, "observation_id", None) or ""
    if oid:
        return oid
    # Fallback: metadata.comment_id populated by observation_from_raw or normalized pipeline
    md = getattr(r.content, "metadata", None) or {}
    if isinstance(md, dict) and md.get("comment_id"):
        return "tiktok:" + str(md["comment_id"])
    # Last resort: object identity (ensures no silent drops)
    return "raw:" + str(id(r))


def _parent_id(r: Observation) -> str:
    """Parent comment id dari metadata — consistent dengan dedup._parent."""
    md = getattr(r.content, "metadata", None) or {}
    if isinstance(md, dict):
        return md.get("parent_comment_id", "")
    return ""


@dataclass
class ThreadNode:
    """Satu node dalam conversation tree."""
    observation: Observation
    children: List["ThreadNode"] = field(default_factory=list)

    @property
    def observation_id(self) -> str:
        return self.observation.observation_id


def build_threads(observations: List[Observation]) -> List[ThreadNode]:
    """Build conversation trees. Returns root nodes (top-level + orphans)."""
    nodes: Dict[str, ThreadNode] = {}
    for r in observations:
        key = _index_key(r)
        if key not in nodes:
            nodes[key] = ThreadNode(observation=r)

    roots: List[ThreadNode] = []
    for r in observations:
        key = _index_key(r)
        if key not in nodes:
            continue
        node = nodes[key]
        pid = _parent_id(r)
        if pid and pid != key and pid in nodes:
            nodes[pid].children.append(node)
        else:
            roots.append(node)
    return roots


def flatten_threads(roots: List[ThreadNode]) -> List[Observation]:
    """Flatten tree to list (pre-order) — useful for export/verify."""
    out: List[Observation] = []
    for n in roots:
        out.append(n.observation)
        if n.children:
            out.extend(flatten_threads(n.children))
    return out


def count_threads(roots: List[ThreadNode]) -> Dict[str, int]:
    """Summary: roots, total nodes, replies (non-root)."""
    flat = flatten_threads(roots)
    return {"roots": len(roots), "nodes": len(flat), "replies": len(flat) - len(roots)}
