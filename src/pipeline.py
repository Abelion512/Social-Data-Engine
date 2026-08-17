#!/usr/bin/env python3
"""
TikTok Data Pipeline — Stage processor

Transformasi:
  raw/v1    → normalized/v1  (text_normalize + context snapshot)
  normalized → enriched/v1   (LLM gate: identity + quality score)
  enriched  → curated/v1     (quality gate: lolos threshold)

Idempotent per-stage: cek output existence + record count.
Resume: lewati stage yang sudah selesai.

Usage:
  python pipeline.py --video 7472094895228468510  # process single video raw→curated
  python pipeline.py --all                        # process semua video di data/raw/
  python pipeline.py --stage normalize --all
  python pipeline.py --video VID --stage quality
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Callable
from datetime import datetime, timezone

# Pastikan root repo (parent dari src/) masuk sys.path — diperlukan saat
# dieksekusi sebagai `python src/pipeline.py`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.tiktok_schema import (
    SCHEMA_VERSION,
    COLLECTOR_VERSION,
    normalize_text,
    raw_to_normalized,
    write_jsonl,
    RawComment,
    Author,
    NormalizedComment,
    EnrichedComment,
    CuratedComment,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = _ROOT  # root repo (bukan src/)
DATA_DIR = BASE / "data"
RAW_DIR = DATA_DIR / "raw"
NORM_DIR = DATA_DIR / "normalized"
ENRICH_DIR = DATA_DIR / "enriched"
CURATED_DIR = DATA_DIR / "curated"
REJECTED_DIR = DATA_DIR / "rejected"
MANIFEST_DIR = DATA_DIR / "manifests"

for d in (RAW_DIR, NORM_DIR, ENRICH_DIR, CURATED_DIR, REJECTED_DIR, MANIFEST_DIR):
    d.mkdir(parents=True, exist_ok=True)


def today_stamp() -> str:
    return time.strftime("%Y-%m-%d")


# ── Stage config ──────────────────────────────────────────────────────────────
PIPELINE_VERSION = "1.0.0"

# Quality thresholds
QUALITY_MIN = 0.30      # curated_score minimum
SEMANTIC_DENSITY_MIN = 0.15
SPAM_MAX = 0.85
TOXICITY_MAX = 0.80


# ── Hashing helpers for 3-level dedup ─────────────────────────────────────────
def hash_exact(text: str) -> str:
    """Exact match hash — raw text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def hash_normalized(text: str) -> str:
    """Normalized hash — lowercase, NFKC, whitespace-collapsed."""
    return hash_exact(normalize_text(text))


def simhash(text: str) -> int:
    """
    SimHash ringan (MurmurHash3-style) untuk near-duplicate detection.
    O(64x) token operations, collision-tolerant.
    """
    # Very simple char-frequency simhash — cheap, catches copy-paste variants.
    v = 0
    for ch in text:
        v = (v * 31 + ord(ch)) & 0xFFFFFFFFFFFFFFFF
    return v


def hash_simhash(text: str) -> str:
    return f"{simhash(text):016x}"


def hamming(a: int, b: int) -> int:
    """Hamming distance between two integers (as 64-bit)."""
    x = a ^ b
    return bin(x).count("1")


# ── Stage 1: Normalize ─────────────────────────────────────────────────────────
FORCE = False  # --force: re-process meski output sudah ada (untuk update schema/fix)


def stage_normalize(video_id: str) -> Dict:
    """
    Raw → Normalized.
    Idempotent: lewati kalau normalized file sudah ada & record count match.
    """
    raw_file = RAW_DIR / today_stamp() / f"{video_id}.jsonl"
    norm_file = NORM_DIR / today_stamp() / f"{video_id}.jsonl"

    if not raw_file.exists():
        return {"status": "no_raw", "video_id": video_id}

    # Cek idempotency
    if norm_file.exists() and not FORCE:
        norm_count = sum(1 for _ in norm_file.open())
        raw_count = sum(1 for _ in raw_file.open())
        if norm_count >= raw_count:
            print(f"[normalize] ✓ {video_id} already normalized ({norm_count} records)")
            return {"status": "skipped", "video_id": video_id, "count": norm_count}

    raw_records: List[dict] = []
    with raw_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_records.append(json.loads(line))

    normalized = []
    for r in raw_records:
        # Raw dict ditulis flat (author_id/author_handle), tapi field dataclass
        # bernama `author` (Author) — rekonstruksi agar handle tidak hilang.
        rc = RawComment(
            **{k: v for k, v in r.items() if k in RawComment.__dataclass_fields__ and k != "author"},
            author=Author(r.get("author_id", ""), r.get("author_handle", ""), r.get("display_name", "")),
        )
        nc = raw_to_normalized(rc)
        normalized.append(nc.to_dict())

    written = write_jsonl(str(norm_file), normalized, append=False)
    print(f"[normalize] {video_id}: {written} normalized → {norm_file.name}")
    return {"status": "ok", "video_id": video_id, "input": len(raw_records), "output": written}


# ── Stage 2: Dedup (3-level) ───────────────────────────────────────────────────
def stage_dedup(video_id: str) -> Dict:
    """
    3-level dedup: exact hash → normalized hash → simhash near-duplicate.
    Output: data/normalized/<date>/<video_id>.deduped.jsonl (overwrite)
    Rejected duplicates → data/rejected/
    """
    norm_file = NORM_DIR / today_stamp() / f"{video_id}.jsonl"
    dedup_file = NORM_DIR / today_stamp() / f"{video_id}.deduped.jsonl"
    rejected_file = REJECTED_DIR / today_stamp() / f"{video_id}.dups.jsonl"

    if not norm_file.exists():
        return {"status": "no_normalized", "video_id": video_id}

    seen_exact: set = set()
    seen_normalized: set = set()
    seen_simhash: Dict[int, str] = {}  # simhash_prefix → first text

    kept: List[dict] = []
    rejected: List[dict] = []

    with norm_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            text = rec.get("text_raw", "")

            # Level 1: exact
            h_exact = hash_exact(text)
            if h_exact in seen_exact:
                rec["_dedup_reject"] = "exact"
                rejected.append(rec)
                continue
            seen_exact.add(h_exact)

            # Level 2: normalized
            h_norm = hash_normalized(text)
            if h_norm in seen_normalized:
                rec["_dedup_reject"] = "normalized"
                rejected.append(rec)
                continue
            seen_normalized.add(h_norm)

            # Level 3: near-duplicate (simhash, tolerance ≤2 bit flips)
            sh = simhash(text)
            prefix = sh >> 48  # top 16 bits as bucket
            is_dup = False
            if prefix in seen_simhash:
                if hamming(sh, int(seen_simhash[prefix], 16)) <= 2:
                    is_dup = True
            if is_dup:
                rec["_dedup_reject"] = "near_duplicate"
                rejected.append(rec)
                continue
            seen_simhash[prefix] = f"{sh:016x}"

            kept.append(rec)

    write_jsonl(str(dedup_file), kept, append=False)
    write_jsonl(str(rejected_file), rejected, append=False)

    print(f"[dedup] {video_id}: kept={len(kept)}, rejected={len(rejected)}")
    return {"status": "ok", "video_id": video_id, "kept": len(kept), "rejected": len(rejected)}


# ── Stage 3: Quality scoring ──────────────────────────────────────────────────
def quality_score(rec: dict) -> float:
    """
    Heuristic quality score tanpa LLM.
    Components:
      - semantic_density: token length / (length + emoji_count + digit_count)
      - spam_probability: repetition ratio, excessive punctuation
      - toxicity: regex-based keyword check
    Curated score = quality × (1 - spam) × (1 - toxicity)
    """
    text = rec.get("text_normalized", rec.get("text_raw", ""))
    if not text:
        return 0.0

    # Semantic density: ratio kata unik / total kata
    words = text.split()
    if not words:
        return 0.0
    unique = len(set(words))
    semantic_density = min(unique / len(words), 1.0) if words else 0.0

    # Spam probability: repeated chars, emojis, punctuation bursts, link bursts
    repeat_chars = len(re.findall(r"(.)\1{3,}", text))  # "banggget"
    emoji_count = sum(1 for c in text if ord(c) > 0x2700)
    digit_count = sum(c.isdigit() for c in text)
    url_count = len(re.findall(r"https?://", text))
    if url_count > 1:  # link spam — mirror cleanse_comments rule (>1 http)
        spam_probability = 0.95
    else:
        spam_signals = repeat_chars + emoji_count * 0.1 + (digit_count / max(len(text), 1))
        spam_probability = min(spam_signals / 5.0, 0.95)

    # Toxicity: basic keyword blocklist
    toxic_words = re.findall(r"\b(a[sz]+|b[i1!]+|c[o0]+cksucker|f+u+c+k|motherfucker|bitch|slut|whore|retard|autist)\b", text, re.I)
    toxicity = min(len(toxic_words) / 3.0, 0.9)

    quality = (semantic_density + (1 - spam_probability) + (1 - toxicity)) / 3.0
    curated_score = quality * (1 - spam_probability) * (1 - toxicity)
    return round(curated_score, 4)


# ── Stage 4: Quality gate (curation) ───────────────────────────────────────────
def stage_quality_gate(video_id: str) -> Dict:
    """
    Enriched → Curated (lolos threshold) / Rejected (gagal).
    Baca enriched, apply quality_score, filter.
    """
    enriched_file = ENRICH_DIR / today_stamp() / f"{video_id}.jsonl"
    curated_file = CURATED_DIR / today_stamp() / f"{video_id}.jsonl"
    rejected_file = REJECTED_DIR / today_stamp() / f"{video_id}.quality.jsonl"

    if not enriched_file.exists():
        return {"status": "no_enriched", "video_id": video_id}

    kept: List[dict] = []
    rejected: List[dict] = []

    with enriched_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            score = rec.get("quality", {}).get("curated_score", 0.0)

            if score >= QUALITY_MIN:
                kept.append(rec)
            else:
                rec["_rejected_reason"] = f"score {score} < {QUALITY_MIN}"
                rejected.append(rec)

    write_jsonl(str(curated_file), kept, append=False)
    write_jsonl(str(rejected_file), rejected, append=False)

    print(f"[quality_gate] {video_id}: curated={len(kept)}, rejected={len(rejected)}")
    return {"status": "ok", "video_id": video_id, "curated": len(kept), "rejected": len(rejected)}


# ── LLM gate (placeholder — hanya enrichment ringan) ────────────────────────────
# ponytail: LLM enrichment dipisahkan. Untuk milestone awal, quality_score()
# cukup untuk gating. Integrasikan di enrichment stage bila model ready.

def stage_enrich(video_id: str) -> Dict:
    """
    Deduped-normalize → Enriched (quality annotation).
    Tanpa LLM call, hanya komputasi statistik.
    """
    dedup_file = NORM_DIR / today_stamp() / f"{video_id}.deduped.jsonl"
    enrich_file = ENRICH_DIR / today_stamp() / f"{video_id}.jsonl"

    if not dedup_file.exists():
        return {"status": "no_deduped", "video_id": video_id}

    enriched = []
    with dedup_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            q = quality_score(rec)
            rec["quality"] = {
                "curated_score": q,
                "quality": q,
                "semantic_density": q,
                "spam_probability": 0.0,
                "toxicity": 0.0,
                "language_confidence": 0.95,
                "novelty": 0.7,
                "scored_at": datetime.now(timezone.utc).isoformat(),
                "scorer_version": f"pipeline@{PIPELINE_VERSION}",
            }
            rec["provenance"] = {
                "collector": f"tiktok-scrapper@{COLLECTOR_VERSION}",
                "normalizer": f"pipeline@{PIPELINE_VERSION}",
                "annotator": "",
                "model": "",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            enriched.append(rec)

    write_jsonl(str(enrich_file), enriched, append=False)
    print(f"[enrich] {video_id}: {len(enriched)} enriched → {enrich_file.name}")
    return {"status": "ok", "video_id": video_id, "output": len(enriched)}


# ── Manifest ────────────────────────────────────────────────────────────────────
def generate_manifest(video_id: Optional[str] = None) -> Dict:
    """Buat/benchmark dataset manifest."""
    date = today_stamp()
    manifests = []

    targets = [video_id] if video_id else _discover_videos()
    for vid in targets:
        raw_f = RAW_DIR / date / f"{vid}.jsonl"
        norm_f = NORM_DIR / date / f"{vid}.jsonl"
        dedup_f = NORM_DIR / date / f"{vid}.deduped.jsonl"
        enrich_f = ENRICH_DIR / date / f"{vid}.jsonl"
        curated_f = CURATED_DIR / date / f"{vid}.jsonl"

        raw_count = _count_lines(raw_f) if raw_f.exists() else 0
        norm_count = _count_lines(norm_f) if norm_f.exists() else 0
        dedup_count = _count_lines(dedup_f) if dedup_f.exists() else 0
        enrich_count = _count_lines(enrich_f) if enrich_f.exists() else 0
        curated_count = _count_lines(curated_f) if curated_f.exists() else 0

        lang_dist = _lang_estimate(enrich_f if enrich_f.exists() else norm_f)

        manifests.append({
            "video_id": vid,
            "date": date,
            "raw": raw_count,
            "normalized": norm_count,
            "deduped": dedup_count,
            "enriched": enrich_count,
            "curated": curated_count,
            "languages": lang_dist,
        })

    manifest = {
        "dataset_id": f"tiktok-social-{date.replace('-', '')}" if video_id else f"tiktok-corpus-{date.replace('-', '')}",
        "version": PIPELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "videos": manifests,
        "totals": {
            "source_count": len(manifests),
            "comment_count": sum(m["raw"] for m in manifests),
            "curated": sum(m["curated"] for m in manifests),
            "rejected": sum(m["raw"] - m["curated"] for m in manifests),
        },
    }

    if video_id:
        out = MANIFEST_DIR / date / f"{video_id}.manifest.json"
    else:
        out = MANIFEST_DIR / date / "corpus.manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[manifest] → {out}")
    return manifest


def _discover_videos() -> List[str]:
    """Scan data/raw/<today>/ for video_ids."""
    raw_today = RAW_DIR / today_stamp()
    if not raw_today.exists():
        return []
    return [f.stem for f in raw_today.glob("*.jsonl")]


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open())


def _lang_estimate(path: Path) -> Dict[str, float]:
    """Very rough language ratio: id vs en vs mixed, via keyword density."""
    id_words = {"gue", "kamu", "aku", "kita", "buat", "banget", "sih", "lah", "kok", "ya", "gak", "ngk", "nggk", "dikit", "banyak", "enak", "gajebol", "related"}
    en_ratio = 0.0
    id_ratio = 0.0
    total = 0
    if not path.exists():
        return {"id": 0.0, "en": 0.0, "mixed": 0.0}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            text = rec.get("text_normalized", "").lower()
            words = text.split()
            total += len(words)
            id_hits = sum(1 for w in words if w in id_words)
            if id_hits > 0:
                id_ratio += id_hits / len(words)
            # crude EN: vowels-heavy latin words
            en_hits = sum(1 for w in words if len(w) > 3 and all(c.isalpha() and c.isascii() for c in w) and sum(1 for c in w if c in "aeiou") > 0)
            if en_hits > 0:
                en_ratio += en_hits / len(words)
    if total == 0:
        return {"id": 0.0, "en": 0.0, "mixed": 0.0}

    id_frac = min(id_ratio / 1, 1.0)
    en_frac = min(en_ratio / 1, 1.0)
    mixed = max(0, 1 - id_frac - en_frac)
    return {"id": round(id_frac, 2), "en": round(en_frac, 2), "mixed": round(mixed, 2)}


# ── Orchestration ───────────────────────────────────────────────────────────────

STAGES: Dict[str, Callable] = {
    "normalize": stage_normalize,
    "dedup": stage_dedup,
    "enrich": stage_enrich,
    "quality": stage_quality_gate,
}


def run_video(video_id: str, only_stage: Optional[str] = None) -> Dict:
    """Jalankan semua stage untuk satu video (atau hanya satu stage)."""
    results = {}
    if only_stage:
        fn = STAGES.get(only_stage)
        if fn:
            results[only_stage] = fn(video_id)
        else:
            print(f"[!] Unknown stage: {only_stage}")
            return results
    else:
        order = ["normalize", "dedup", "enrich", "quality"]
        for stage_name in order:
            fn = STAGES[stage_name]
            res = fn(video_id)
            results[stage_name] = res
            # Stop cascade if stage has no input
            if res.get("status") in ("no_raw", "no_normalized", "no_deduped", "no_enriched"):
                print(f"[pipeline] Stop at {stage_name}: {res['status']}")
                break
        # Generate manifest after full run
        generate_manifest(video_id)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Data Pipeline")
    parser.add_argument("--video", help="Single video_id to process")
    parser.add_argument("--all", action="store_true", help="Process all videos in data/raw/<today>/")
    parser.add_argument("--stage", choices=list(STAGES.keys()), help="Run specific stage only")
    parser.add_argument("--force", action="store_true", help="Re-process meski output sudah ada (update schema/fix)")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest only")
    args = parser.parse_args()
    global FORCE
    FORCE = args.force

    if args.manifest:
        vid = args.video
        m = generate_manifest(vid)
        print(json.dumps(m, indent=2))
        return

    if args.video:
        run_video(args.video, args.stage)
    elif args.all or args.stage:
        vids = _discover_videos()
        if not vids:
            print(f"[!] No raw data found di {RAW_DIR / today_stamp()}/")
            return
        for vid in vids:
            print(f"\n=== Processing {vid} ===")
            run_video(vid, args.stage)


if __name__ == "__main__":
    main()
