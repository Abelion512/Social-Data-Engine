#!/usr/bin/env python3
"""
Multi-Tier Deduplication — exact, normalized, near-duplicate.

Tiga tingkat dedup untuk menangani variasi berbeda:
  1. Exact: hash text mentah, skip duplikat persis
  2. Normalized: lowercase + unicode/whitespace normalize, skip after folding
  3. Near-duplicate: Jaccard similarity pada bigram, skip jika similarity > threshold

Semua tingkat adalah **per-parent** (thread-aware): kunci dedup = (parent_comment_id, text).
Alasannya: 7 reply dengan teks sama ("connect kak") ke 7 orang/parent yang BERBEDA adalah
7 reply unik — bukan duplikat. Hanya reply yang SAMA ke parent YANG SAMA yang dianggap dupe.
Live test (@enxayeti) membuktikan: tanpa per-parent key, 7 threaded replies (distinct parents)
diserap semua → curated 0 reply; dengan ini 7 survivor, 1 per parent.

ponytail: dedup_near_duplicate O(n²) pada bigram Jaccard; upgrade ke MinHash
bila corpus melebihi 10k record.
"""
from __future__ import annotations

from typing import List, Set
from collections import Counter

from src.schema.canonical import Observation
from src.tiktok_schema import normalize_text


def _parent(r: Observation) -> str:
    """Parent id dari metadata (thread-aware key)."""
    md = getattr(r.content, "metadata", None) or {}
    if isinstance(md, dict):
        return md.get("parent_comment_id", "")
    return ""


def dedup_exact(records: List[Observation]) -> List[Observation]:
    """Tier 1: exact text dedup — hash (parent, text). per-parent."""
    seen: Set[tuple] = set()
    out = []
    for r in records:
        key = (_parent(r), r.content.text_raw.strip())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def dedup_normalized(records: List[Observation]) -> List[Observation]:
    """Tier 2: normalized dedup — per-parent, setelah case/whitespace/unicode fold."""
    seen: Set[tuple] = set()
    out = []
    for r in records:
        key = (_parent(r), normalize_text(r.content.text_raw))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _bigrams(text: str) -> Counter:
    """Karakter bigram counter untuk Jaccard similarity."""
    t = normalize_text(text)
    return Counter(t[i:i + 2] for i in range(len(t) - 1))


def _jaccard(a: Counter, b: Counter) -> float:
    """Hitung Jaccard similarity antara dua Counter."""
    if not a or not b:
        return 0.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def dedup_near_duplicate(records: List[Observation], threshold: float = 0.85) -> List[Observation]:
    """
    Tier 3: near-duplicate dedup — Jaccard on character bigrams, PER PARENT.

    Reply dengan teks sama tapi ke parent/ thread berbeda tidak dikategorikan
    duplikat (bigram comparison hanya per-grup parent yang sama).

    ponytail: O(n²) similarity scan; upgrade to MinHash + LSH bila corpus > 10k.
    """
    kept = []
    seen_bgs_per_parent: dict[str, List[Counter]] = {}
    for r in records:
        parent = _parent(r)
        bg = _bigrams(r.content.text_raw)
        bucket = seen_bgs_per_parent.setdefault(parent, [])
        if any(_jaccard(bg, prev) >= threshold for prev in bucket):
            continue
        bucket.append(bg)
        kept.append(r)
    return kept


def dedup_all(records: List[Observation], threshold: float = 0.85) -> List[Observation]:
    """Apply semua tiga tingkat — urutan penting: exact → normalized → near.
    Per-parent (thread-aware) sehingga reply ke parent beda tidak diserap."""
    step1 = dedup_exact(records)
    step2 = dedup_normalized(step1)
    step3 = dedup_near_duplicate(step2, threshold=threshold)
    return step3
