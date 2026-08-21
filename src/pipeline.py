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
import json
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
    COLLECTOR_VERSION,
    normalize_text,
    raw_to_normalized,
    write_jsonl,
    RawComment,
    Author,
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
GATING_THRESHOLD = 0.15  # quality_score minimum utk LLM call (hemat token)
LLM_MAX_TIMEOUT = 120    # max seconds untuk seluruh LLM enrichment stage

# ── 9Router LLM config (shared via src/config.py) ───────────────────────────
from src.config import LLM_API, LLM_KEY, LLM_MODEL, ENRICH_MODELS

# ── LLM enrichment: identity extraction via 9Router ──────────────────────────
def llm_enrich_identities(records: List[dict], max_timeout: int = None) -> List[Optional[dict]]:
    """
    Infer real names + companies dari komentar via 9Router (OpenAI-compatible).
    Batch processing: 10 records per API call, chain fallback ENRICH_MODELS.

    Args:
        records: List of enriched records to process.
        max_timeout: Max seconds for entire operation (default: LLM_MAX_TIMEOUT).

    Returns list of identity dicts (same order as input), None if not inferable.
    """
    import requests as _req

    if max_timeout is None:
        max_timeout = LLM_MAX_TIMEOUT
    start_time = time.time()
    identities: List[Optional[dict]] = []
    BATCH = 10
    for start in range(0, len(records), BATCH):
        # Timeout check
        elapsed = time.time() - start_time
        if elapsed > max_timeout:
            print(f"[!] llm_enrich timeout ({max_timeout}s), processed {start}/{len(records)} records")
            identities.extend([None] * (len(records) - start))
            break
        chunk = records[start:start + BATCH]
        batch = [{
            "username": r.get("author_handle", ""),
            "display_name": r.get("display_name", ""),
            "comment": r.get("text_raw", "")[:200],
        } for r in chunk]

        prompt = f"""Analyze TikTok commenters. Infer real identity.

For each:
- real_name: actual name (from display_name or context). Only if display_name looks like a real name (e.g. "John Smith"), not usernames like "funny_cat_42"
- company: employer/company if mentioned or inferable
- role: job title if mentioned
- linkedin_hint: best search query for LinkedIn
- confidence: 0-1

Commenters:
{json.dumps(batch, indent=2, ensure_ascii=False)}

Return JSON array. null if not inferable."""

        headers = {"Content-Type": "application/json"}
        if LLM_KEY:
            headers["Authorization"] = f"Bearer {LLM_KEY}"

        content = None
        last_err = None
        for model in ENRICH_MODELS:
            for attempt in (1, 2):
                try:
                    resp = _req.post(LLM_API, json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2048,
                        "temperature": 0.1,
                        "stream": False,
                    }, headers=headers, timeout=120)
                    result = resp.json()
                    if "error" in result or "choices" not in result:
                        last_err = f"{model} (try {attempt}): {str(result.get('error', result.keys()))[:150]}"
                        print(f"[!] llm_enrich {last_err}")
                        time.sleep(3 * attempt)
                        continue
                    content = result["choices"][0]["message"]["content"]
                    if content and content.strip():
                        break
                    last_err = f"{model} (try {attempt}): empty content"
                    print(f"[!] llm_enrich {last_err}")
                except Exception as e:
                    last_err = f"{model} (try {attempt}): {type(e).__name__}: {e}"
                    print(f"[!] llm_enrich {last_err}")
                    time.sleep(3 * attempt)
            if content and content.strip():
                break
        if not content or not content.strip():
            print(f"[!] llm_enrich all models failed; last: {last_err}")
            identities.extend([None] * len(chunk))
            continue
        # Parse JSON response (handle markdown fences)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        try:
            parsed = json.loads(content.strip())
            if not isinstance(parsed, list):
                parsed = [parsed]
            identities.extend(parsed[:len(chunk)])
        except json.JSONDecodeError as e:
            print(f"[!] llm_enrich parse fail: {e}; content={content[:300]!r}")
            identities.extend([None] * len(chunk))
    return identities


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
            parent = rec.get("parent_comment_id", "") or ""
            # thread-aware dedup: replies with the SAME text to DIFFERENT
            # parents are NOT duplicates (e.g. 7x 'connect kak' replies to
            # 7 different top-level comments = 7 distinct threaded replies).
            # Fold parent into every dedup key so only exact/near duplicates
            # *within the same parent* are rejected.
            keyed = (parent + "\x00" + text) if parent else text

            # Level 1: exact
            h_exact = hash_exact(keyed)
            if h_exact in seen_exact:
                rec["_dedup_reject"] = "exact"
                rejected.append(rec)
                continue
            seen_exact.add(h_exact)

            # Level 2: normalized
            h_norm = hash_normalized(keyed)
            if h_norm in seen_normalized:
                rec["_dedup_reject"] = "normalized"
                rejected.append(rec)
                continue
            seen_normalized.add(h_norm)

            # Level 3: near-duplicate (simhash, tolerance ≤2 bit flips)
            sh = simhash(keyed)
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
    # URL detection: protocol-prefixed + bare domains (www.example.com)
    url_count = len(re.findall(r"https?://|\bwww\.[\w.-]+\.[a-z]{2,}", text, re.I))
    if url_count > 1:  # link spam — >1 URL = suspicious
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


# ── Stage 3: Enrich (heuristic + LLM identity via 9Router) ───────────────────
def stage_enrich(video_id: str) -> Dict:
    """
    Deduped-normalize → Enriched (quality annotation + LLM identity).

    Flow:
      1. Heuristic quality_score() untuk semua komentar
      2. Gating: hanya kirim ke LLM kalau score >= GATING_THRESHOLD
      3. LLM identity extraction via 9Router (chain fallback → claude-work)
      4. Merge identity ke enriched records
    """
    dedup_file = NORM_DIR / today_stamp() / f"{video_id}.deduped.jsonl"
    enrich_file = ENRICH_DIR / today_stamp() / f"{video_id}.jsonl"

    if not dedup_file.exists():
        return {"status": "no_deduped", "video_id": video_id}

    # Step 1: Read + heuristic scoring
    enriched: List[dict] = []
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

    # Step 2: Gating — filter records yang lolos threshold utk LLM call
    gated = [r for r in enriched if r["quality"]["curated_score"] >= GATING_THRESHOLD]
    skipped = len(enriched) - len(gated)
    print(f"[enrich] {video_id}: {len(enriched)} records, {len(gated)} pass gate (>= {GATING_THRESHOLD}), {skipped} skipped")

    # Step 3: LLM identity extraction via 9Router
    if gated and LLM_KEY:
        print(f"[enrich] {video_id}: calling 9Router for identity extraction ({len(gated)} records)...")
        try:
            identities = llm_enrich_identities(gated)
            # Map identities back ke enriched records (hanya yang gated)
            gated_idx = 0
            for i, rec in enumerate(enriched):
                if rec["quality"]["curated_score"] >= GATING_THRESHOLD:
                    identity = identities[gated_idx] if gated_idx < len(identities) else None
                    rec["identity"] = identity
                    if identity:
                        rec["provenance"]["annotator"] = f"llm@{LLM_MODEL}"
                        rec["provenance"]["model"] = LLM_MODEL
                    gated_idx += 1
            n_identity = sum(1 for r in enriched if r.get("identity") and r["identity"].get("real_name"))
            print(f"[enrich] {video_id}: {n_identity} identities found via LLM")
        except Exception as e:
            print(f"[!] llm_enrich failed: {e}")
    elif not LLM_KEY:
        print(f"[enrich] {video_id}: no 9Router API_KEY — identity extraction skipped")

    # Step 4: Write enriched output
    write_jsonl(str(enrich_file), enriched, append=False)
    n_with_id = sum(1 for r in enriched if r.get("identity") and r["identity"].get("real_name"))
    print(f"[enrich] {video_id}: {len(enriched)} enriched ({n_with_id} with identity) → {enrich_file.name}")
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
    parser.add_argument("--export-mark", action="store_true", help="Export curated → MARK-ready JSON")
    args = parser.parse_args()
    global FORCE
    FORCE = args.force

    if args.manifest:
        vid = args.video
        m = generate_manifest(vid)
        print(json.dumps(m, indent=2))
        return

    if args.export_mark:
        from src.mark_export import export_video, export_all
        if args.video:
            result = export_video(args.video)
        else:
            result = export_all()
        print(json.dumps(result, indent=2))
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
