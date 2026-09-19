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

Performa (tier 3): Jaccard bigram dihitung PER-WINDOW ukuran, bukan semua-pasangan.
Jaccard multiset selalu memenuhi `J <= min(|A|,|B|) / max(|A|,|B|)`, jadi pasangan
yang rasio ukurannya < threshold tidak mungkin duplikat → di-skip sebelum
menyentuh bigram. Hasilnya identik dengan scan penuh (dibuktikan test diferensial
di `tests/test_dedup_scaling.py`), tapi bukan lagi O(n²).

ponytail: window-prune + overlap iteratif sudah cukup di skala corpus sekarang
(5k record: 690k → ribuan perbandingan). Naik ke MinHash/LSH hanya bila ukuran
di dalam satu bucket parent seragam (uniform) dan melewati ~50k record.
"""
from __future__ import annotations

import hashlib

from bisect import bisect_left, bisect_right
from typing import List, Set, Tuple
from collections import Counter

from src.schema.canonical import Observation
from src.tiktok_schema import normalize_text


# ── Hashing helpers (3-tier dedup keys) ───────────────────────────────────────
def hash_exact(text: str) -> str:
    """Exact match hash — raw text (16-hex-char sha256 prefix)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parent(r: Observation) -> str:
    """Parent id dari metadata (thread-aware key)."""
    md = getattr(r.content, "metadata", None) or {}
    if isinstance(md, dict):
        return md.get("parent_comment_id", "")
    return ""


def hash_normalized(text: str) -> str:
    """Normalized hash — lowercase, NFKC, whitespace-collapsed."""
    return hash_exact(normalize_text(text))


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
    """Karakter bigram counter untuk Jaccard similarity (multiset)."""
    t = normalize_text(text)
    return Counter(t[i:i + 2] for i in range(len(t) - 1))


def _size(bg: Counter) -> int:
    """Total token bigram (multiset size) — sum(counter.values())."""
    return sum(bg.values())


def _overlap(a: Counter, b: Counter) -> int:
    """Multiset intersection size tanpa membangun Counter baru.

    `sum((a & b).values())` versi lama memanggil Counter.__and__ (Python-level)
    untuk setiap pasangan; loop ini hanya menyentuh dict terkecil dengan `get`.
    """
    if len(a) > len(b):
        a, b = b, a
    get = b.get
    total = 0
    for k, v in a.items():
        w = get(k, 0)
        if w:
            total += v if v < w else w
    return total


def _jaccard(a: Counter, b: Counter, size_a: int = None, size_b: int = None) -> float:
    """Jaccard similarity (multiset) = overlap / (|A| + |B| - overlap).

    Setara persis dengan `sum((a & b)) / sum((a | b))`, tanpa alokasi dua Counter
    per pasangan. Ukuran boleh diberikan agar tidak dihitung ulang.
    """
    sa = _size(a) if size_a is None else size_a
    sb = _size(b) if size_b is None else size_b
    if not sa or not sb:
        return 0.0
    inter = _overlap(a, b)
    union = sa + sb - inter
    return inter / union if union else 0.0


def _candidate_range(sizes: List[int], size: int, threshold: float) -> Tuple[int, int]:
    """Rentang index partner yang MASIH MUNGKIN duplikat dengan `size`.

    J <= min/max, jadi hanya partner dengan `min(size, p) >= threshold*max(size, p)`
    yang layak diperiksa: p ∈ [threshold*size, size/threshold]. Batasnya dilebarkan
    1 token di kedua sisi supaya pembulatan float tak pernah membuang pasangan valid.
    `sizes` harus terurut naik (itulah kenapa bucket disisipkan secara terurut).
    """
    if threshold <= 0 or not sizes:
        return 0, len(sizes)
    lo = max(0, int(threshold * size) - 1)
    hi = int(size / threshold) + 1
    return bisect_left(sizes, lo), bisect_right(sizes, hi)


def dedup_near_duplicate(records: List[Observation], threshold: float = 0.85) -> List[Observation]:
    """
    Tier 3: near-duplicate dedup — Jaccard on character bigrams, PER PARENT.

    Reply dengan teks sama tapi ke parent/ thread berbeda tidak dikategorikan
    duplikat (bigram comparison hanya per-grup parent yang sama).

    Kompleksitas: O(n · w) dengan w = ukuran window (bukan O(n²)). Urutan record
    yang bertahan sama seperti scan penuh karena hanya pasangan yang secara
    matematis mustahil >= threshold yang dilewati.
    """
    kept: List[Observation] = []
    # Per parent: daftar ukuran (terurut naik, untuk bisect) + bigram sejajar.
    sizes_per_parent: dict[str, List[int]] = {}
    bgs_per_parent: dict[str, List[Counter]] = {}
    for r in records:
        parent = _parent(r)
        bg = _bigrams(r.content.text_raw)
        size = _size(bg)
        sizes = sizes_per_parent.setdefault(parent, [])
        bgs = bgs_per_parent.setdefault(parent, [])
        if size:
            left, right = _candidate_range(sizes, size, threshold)
            if any(_jaccard(bg, bgs[i], size, sizes[i]) >= threshold
                   for i in range(left, right)):
                continue
            at = bisect_right(sizes, size)
            sizes.insert(at, size)     # urutan naik dipertahankan → bisect valid
            bgs.insert(at, bg)
        kept.append(r)                 # teks tanpa bigram tak pernah match, tetap disimpan
    return kept


def dedup_all(records: List[Observation], threshold: float = 0.85) -> List[Observation]:
    """Apply semua tiga tingkat — urutan penting: exact → normalized → near.
    Per-parent (thread-aware) sehingga reply ke parent beda tidak diserap."""
    step1 = dedup_exact(records)
    step2 = dedup_normalized(step1)
    step3 = dedup_near_duplicate(step2, threshold=threshold)
    return step3
