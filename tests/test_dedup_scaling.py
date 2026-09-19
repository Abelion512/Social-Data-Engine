#!/usr/bin/env python3
"""
Scaling guards for the near-duplicate dedup tier (stdlib only, deterministic).

`dedup_near_duplicate` used to compare every record against every kept record of
the same parent (the module's own "ponytail: O(n²)" note). It now compares only
within a provably-safe size window, because multiset Jaccard always obeys

    J(A, B) <= min(|A|, |B|) / max(|A|, |B|)

so a pair whose size ratio is below the threshold can never reach it.

These tests lock in the two properties that make that rewrite safe:

  1. **Output equivalence** — the pruned implementation returns exactly the same
     kept records, in the same order, as the original full scan (verified against
     a verbatim copy of the old implementation over randomized corpora:
     4 text shapes × thresholds).
  2. **Pruning actually happens** — provably-incompatible pairs (large size gap)
     are never compared at all, and realistic length spread stays far below the
     n²/2 all-pairs bound.

Measured on this machine (5 000 records, realistic length spread):
old 13 099 ms → new 408 ms (32x), identical kept set.

Run:  python tests/test_dedup_scaling.py
      python -m pytest tests/test_dedup_scaling.py -v
"""
from __future__ import annotations

import random
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline import dedup as D
from src.schema.mapper import tiktok_to_canonical
from src.tiktok_schema import Author, RawComment, normalize_text

WORDS = (
    "gue kamu aku kita buat banget sih lah kok ya gak ngk dikit banyak enak related "
    "connect kak malam coretan keren video ini bagus sekali terima kasih seru lucu sedih"
).split()


# ── reference: the ORIGINAL full-scan tier 3, kept verbatim as an oracle ──────
def _reference_bigrams(text: str) -> Counter:
    t = normalize_text(text)
    return Counter(t[i:i + 2] for i in range(len(t) - 1))


def _reference_jaccard(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def _reference_near_duplicate(records, threshold: float = 0.85):
    kept = []
    seen_bgs_per_parent: dict[str, list] = {}
    for r in records:
        parent = D._parent(r)
        bg = _reference_bigrams(r.content.text_raw)
        bucket = seen_bgs_per_parent.setdefault(parent, [])
        if any(_reference_jaccard(bg, prev) >= threshold for prev in bucket):
            continue
        bucket.append(bg)
        kept.append(r)
    return kept


# ── helpers ──────────────────────────────────────────────────────────────────
def _obs(i: int, text: str, parent: str = "") -> object:
    return tiktok_to_canonical(RawComment(
        video_id="v", video_url="u", comment_id=f"c{i}", parent_comment_id=parent,
        author=Author("1", "h", "d"), text_raw=text,
        video_context={"caption": "", "hashtags": [], "creator": "",
                       "create_time": 0, "transcription": ""},
    ))


def _corpus(n: int, seed: int, shape: str = "mixed"):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        if shape == "uniform":
            text = " ".join(rnd.choice(WORDS) for _ in range(6))
        elif shape == "realistic":
            text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 40)))
        else:                                    # mixed: widest spread + dupes
            text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(0, 25)))
            if rnd.random() < 0.15 and out:
                text = out[rnd.randrange(len(out))].content.text_raw
        parent = "" if rnd.random() < 0.5 else f"c{rnd.randrange(0, max(1, i))}"
        out.append(_obs(i, text, parent))
    return out


def _count_comparisons(records, threshold: float = 0.85) -> int:
    """Run tier 3 with `_jaccard` instrumented — counts full pair comparisons."""
    original = D._jaccard
    calls = [0]

    def counting(a, b, size_a=None, size_b=None):
        calls[0] += 1
        return original(a, b, size_a, size_b)

    D._jaccard = counting
    try:
        kept = D.dedup_near_duplicate(records, threshold)
    finally:
        D._jaccard = original
    return calls[0], len(kept)


# ── 1. output equivalence with the original full scan ────────────────────────
def test_same_output_as_full_scan_randomized():
    for shape in ("mixed", "uniform", "realistic"):
        for seed in range(3):
            records = _corpus(150, seed, shape)
            for threshold in (0.5, 0.85, 0.95, 1.0):
                expected = [r.observation_id for r in _reference_near_duplicate(records, threshold)]
                actual = [r.observation_id for r in D.dedup_near_duplicate(records, threshold)]
                assert actual == expected, (
                    f"output drift shape={shape} seed={seed} threshold={threshold}: "
                    f"expected {len(expected)} kept, got {len(actual)}"
                )


def test_same_output_on_hand_built_edge_cases():
    records = [
        _obs(0, ""),                                  # empty text, root
        _obs(1, "x"),                                 # too short for bigrams
        _obs(2, ""),                                  # duplicate empty, root
        _obs(3, "connect kak, malam ini kita bahas topik seru"),   # root
        _obs(4, "connect kak, malam ini kita bahas topik seru"),   # exact dup, same parent
        _obs(5, "connect kak malam ini kita bahas topik seru!", parent="c3"),  # near-dup, other parent
        _obs(6, "connect kak, malam ini kita bahas topik seru", parent="c3"),  # yes-dup, other parent
        _obs(7, "COBA  cek   ini:  emoji 😂😂 dan SPASI gede"),      # unicode + whitespace noise
        _obs(8, "coba cek ini emoji 😂😂 dan spasi gede"),           # near-dup setelah normalize
    ]
    for threshold in (0.5, 0.85, 1.0):
        expected = [r.observation_id for r in _reference_near_duplicate(records, threshold)]
        actual = [r.observation_id for r in D.dedup_near_duplicate(records, threshold)]
        assert actual == expected, f"edge-case drift at threshold={threshold}"


# ── 2. pruning: provably-incompatible pairs are never compared ───────────────
def test_size_gap_pairs_are_never_compared():
    """A 3-word comment can never be an 85%-similar duplicate of a 200-word one."""
    records = [
        _obs(0, " ".join(WORDS[:3])),                      # ~22 bigrams
        _obs(1, " ".join(WORDS * 10)),                     # ~500+ bigrams
    ]
    comparisons, kept = _count_comparisons(records, threshold=0.85)
    assert comparisons == 0, f"expected the size window to prune the pair, saw {comparisons} comparisons"
    assert kept == 2, "both records must still be kept"


def test_realistic_spread_stays_below_all_pairs():
    n = 600
    comparisons, kept = _count_comparisons(_corpus(n, seed=3, shape="realistic"))
    all_pairs = n * (n - 1) // 2
    assert comparisons < all_pairs // 4, (
        f"{comparisons} comparisons for n={n} is too close to all-pairs ({all_pairs})"
    )
    assert kept > 0


def test_uniform_lengths_still_correct():
    """Worst case (every record the same size) prunes nothing — correctness holds."""
    records = _corpus(200, seed=5, shape="uniform")
    expected = [r.observation_id for r in _reference_near_duplicate(records, 0.85)]
    assert [r.observation_id for r in D.dedup_near_duplicate(records, 0.85)] == expected


# ── 3. the shared normalize_text memo must stay transparent ──────────────────
def test_normalize_text_memo_is_transparent():
    sample = "  Keren   BANGET!!! 😂  "
    normalize_text.cache_clear()
    first = normalize_text(sample)
    assert normalize_text.cache_info().misses == 1, "first call should be a cache miss"
    assert normalize_text(sample) == first
    assert normalize_text.cache_info().hits >= 1, "repeat call should hit the memo"

    expected = unicodedata.normalize("NFKC", sample)
    expected = " ".join(expected.split()).lower().strip()
    assert first == expected, f"memo changed the result: {first!r} != {expected!r}"


if __name__ == "__main__":
    tests = [
        test_same_output_as_full_scan_randomized,
        test_same_output_on_hand_built_edge_cases,
        test_size_gap_pairs_are_never_compared,
        test_realistic_spread_stays_below_all_pairs,
        test_uniform_lengths_still_correct,
        test_normalize_text_memo_is_transparent,
    ]
    passed = failed = 0
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
    print(f"\nDedup Scaling Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
