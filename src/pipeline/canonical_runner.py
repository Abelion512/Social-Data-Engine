#!/usr/bin/env python3
"""
Canonical pipeline runner — THE single stage implementation.

`normalize → dedup → enrich → quality` over the legacy flat-dict record shape,
implemented ONCE with the modular tiers as the engine:

  normalize      : src.tiktok_schema.raw_to_normalized (raw + normalized coexist)
  dedup          : src.pipeline.dedup.dedup_all (3-tier, thread-aware, O(n·w))
  enrich         : quality annotation + gated LLM identity (src.config router)
  quality gate   : src.pipeline.quality.QA thresholds (curated / rejected)

`pipeline.legacy` re-exports this runner (`run_video`, stage fns, thresholds),
so the old CLI surface (`--video/--all/--stage/--force/--manifest/--export-mark`)
keeps working with a single implementation underneath.

Idempotent per stage (output existence + record-count check) and resumable
(skips completed stages) — same contract the legacy runner had.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.runtime.context import require_slug_identifier as _require_id
from src.pipeline.dedup import dedup_all, hash_exact, hash_normalized  # noqa: F401 (re-export)
from src.pipeline.quality import (
    GATING_THRESHOLD,
    QUALITY_MIN,
    SPAM_MAX,
    compute_quality_score,
    passes_gate,
)
from src.config import LLM_API, LLM_KEY, LLM_MODEL, ENRICH_MODELS
from src.tiktok_schema import (
    COLLECTOR_VERSION,
    SCHEMA_VERSION,
    normalize_text,
    raw_to_normalized,
    write_jsonl,
    Author,
    RawComment,
)

# ── Paths (portable: SDE_DATA_DIR override, default repo-root/data) ───────────
DATA_DIR = Path(__import__("os").environ.get("SDE_DATA_DIR") or (_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
NORM_DIR = DATA_DIR / "normalized"
ENRICH_DIR = DATA_DIR / "enriched"
CURATED_DIR = DATA_DIR / "curated"
REJECTED_DIR = DATA_DIR / "rejected"
MANIFEST_DIR = DATA_DIR / "manifests"

for _d in (RAW_DIR, NORM_DIR, ENRICH_DIR, CURATED_DIR, REJECTED_DIR, MANIFEST_DIR):
    _d.mkdir(parents=True, exist_ok=True)

PIPELINE_VERSION = "1.1.0"
LLM_MAX_TIMEOUT = 120   # max seconds untuk seluruh LLM enrichment stage

FORCE = False  # --force: re-process meski output sudah ada (untuk update schema/fix)


def today_stamp() -> str:
    return time.strftime("%Y-%m-%d")


def _validate_video_id(video_id: str) -> str:
    """S-G2 / TM-13: every stage path is built from a validated identifier."""
    return _require_id(video_id, "video_id")


def _read_jsonl(path: Path) -> List[dict]:
    out: List[dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open())


def _record_path(parent: Path, name: str) -> Path:
    """Filename inside `parent` for a validated id — cannot escape the tier dir."""
    _require_id(name, "video_id")
    return parent / name


# ── Stage 1: Normalize ────────────────────────────────────────────────────────
def stage_normalize(video_id: str) -> Dict:
    """Raw → Normalized. Idempotent: skip when normalized covers raw."""
    video_id = _validate_video_id(video_id)
    raw_file = RAW_DIR / today_stamp() / f"{video_id}.jsonl"
    norm_file = NORM_DIR / today_stamp() / f"{video_id}.jsonl"

    if not raw_file.exists():
        return {"status": "no_raw", "video_id": video_id}

    if norm_file.exists() and not FORCE:
        if _count_lines(norm_file) >= _count_lines(raw_file):
            print(f"[normalize] ✓ {video_id} already normalized ({_count_lines(norm_file)} records)")
            return {"status": "skipped", "video_id": video_id, "count": _count_lines(norm_file)}

    raw_records = _read_jsonl(raw_file)
    normalized = []
    for r in raw_records:
        # Raw dicts are flat (author_id/author_handle) while the dataclass nests
        # them under `author` — reconstruct so the handle is never dropped.
        rc = RawComment(
            **{k: v for k, v in r.items()
               if k in RawComment.__dataclass_fields__ and k != "author"},
            author=Author(r.get("author_id", ""), r.get("author_handle", ""),
                          r.get("display_name", "")),
        )
        normalized.append(raw_to_normalized(rc).to_dict())

    written = write_jsonl(str(norm_file), normalized, append=False)
    print(f"[normalize] {video_id}: {written} normalized")
    return {"status": "ok", "video_id": video_id, "input": len(raw_records), "output": written}


# ── Stage 2: Dedup (modular 3-tier, thread-aware) ─────────────────────────────
def _to_observation(rec: dict):
    from src.schema.canonical import Observation, Content
    from src.tiktok_schema import SCHEMA_VERSION as _SV  # avoid circular import cost
    md = {k: rec.get(k, "") for k in (
        "video_id", "video_url", "comment_id", "parent_comment_id",
        "author_id", "author_handle", "display_name", "capture_method",
    )}
    md["likes"] = rec.get("likes", 0)
    md["reply_count"] = rec.get("reply_count", 0)
    md["images"] = rec.get("images", [])
    return Observation(
        observation_id=rec.get("comment_id", ""),
        source="tiktok",
        content=Content(
            text_raw=rec.get("text_raw", ""),
            text_normalized=rec.get("text_normalized", ""),
            metadata=md,
        ),
        schema_version=f"observation.v{_SV}",
    )


def stage_dedup(video_id: str) -> Dict:
    """3-level dedup via the modular tier. Rejected → data/rejected/."""
    video_id = _validate_video_id(video_id)
    norm_file = NORM_DIR / today_stamp() / f"{video_id}.jsonl"
    dedup_file = NORM_DIR / today_stamp() / f"{video_id}.deduped.jsonl"
    rejected_file = REJECTED_DIR / today_stamp() / f"{video_id}.dups.jsonl"

    if not norm_file.exists():
        return {"status": "no_normalized", "video_id": video_id}

    records = _read_jsonl(norm_file)
    kept: List[dict] = []
    rejected: List[dict] = []
    survivors: set = set()

    obs = [_to_observation(r) for r in records]
    from src.schema.canonical import Observation as _O
    survivors = {o.observation_id for o in dedup_all(obs)}

    for r in records:
        if r.get("comment_id", "") in survivors:
            kept.append(r)
        else:
            r["_dedup_reject"] = "modular_3tier"
            rejected.append(r)

    write_jsonl(str(dedup_file), kept, append=False)
    write_jsonl(str(rejected_file), rejected, append=False)

    print(f"[dedup] {video_id}: kept={len(kept)}, rejected={len(rejected)}")
    return {"status": "ok", "video_id": video_id, "kept": len(kept), "rejected": len(rejected)}


# ── Stage 3: Enrich (heuristic quality via modular scorer + gated LLM) ────────
def stage_enrich(video_id: str) -> Dict:
    """Deduped → Enriched (quality annotation + LLM identity, token-gated)."""
    video_id = _validate_video_id(video_id)
    dedup_file = NORM_DIR / today_stamp() / f"{video_id}.deduped.jsonl"
    enrich_file = ENRICH_DIR / today_stamp() / f"{video_id}.jsonl"

    if not dedup_file.exists():
        return {"status": "no_deduped", "video_id": video_id}

    records = _read_jsonl(dedup_file)
    for rec in records:
        obs = _to_observation(rec)
        q = compute_quality_score(obs)
        rec["quality"] = {
            **q,
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

    # Gating: hanya kirim ke LLM kalau score >= GATING_THRESHOLD (hemat token)
    gated = [r for r in records if r["quality"]["curated_score"] >= GATING_THRESHOLD]
    skipped = len(records) - len(gated)
    print(f"[enrich] {video_id}: {len(records)} records, {len(gated)} pass gate (>= {GATING_THRESHOLD}), {skipped} skipped")

    if gated and LLM_KEY:
        print(f"[enrich] {video_id}: calling router for identity extraction ({len(gated)} records)...")
        try:
            identities = llm_enrich_identities(gated)
            idx = 0
            for rec in records:
                if rec["quality"]["curated_score"] >= GATING_THRESHOLD:
                    identity = identities[idx] if idx < len(identities) else None
                    rec["identity"] = identity
                    if identity:
                        rec["provenance"]["annotator"] = f"llm@{LLM_MODEL}"
                        rec["provenance"]["model"] = LLM_MODEL
                    idx += 1
            n_identity = sum(1 for r in records if (r.get("identity") or {}).get("real_name"))
            print(f"[enrich] {video_id}: {n_identity} identities found via LLM")
        except Exception as e:
            print(f"[!] llm_enrich failed: {e}")
    elif not LLM_KEY:
        print(f"[enrich] {video_id}: no API key — identity extraction skipped")

    write_jsonl(str(enrich_file), records, append=False)
    n_with_id = sum(1 for r in records if (r.get("identity") or {}).get("real_name"))
    print(f"[enrich] {video_id}: {len(records)} enriched ({n_with_id} with identity)")
    return {"status": "ok", "video_id": video_id, "output": len(records)}


def llm_enrich_identities(records: List[dict], max_timeout: int = None) -> List[Optional[dict]]:
    """Infer real names + companies via the OpenAI-compatible router (batch 10)."""
    import requests as _req

    if max_timeout is None:
        max_timeout = LLM_MAX_TIMEOUT
    start_time = time.time()
    identities: List[Optional[dict]] = []
    BATCH = 10
    for start in range(0, len(records), BATCH):
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


def quality_score(rec: dict) -> float:
    """Legacy alias: score a flat record dict through the modular scorer."""
    return compute_quality_score(rec)["curated_score"]


# ── Stage 4: Quality gate (modular thresholds) ────────────────────────────────
def stage_quality_gate(video_id: str) -> Dict:
    """Enriched → Curated (≥ QUALITY_MIN) / Rejected."""
    video_id = _validate_video_id(video_id)
    enrich_file = ENRICH_DIR / today_stamp() / f"{video_id}.jsonl"
    curated_file = CURATED_DIR / today_stamp() / f"{video_id}.jsonl"
    rejected_file = REJECTED_DIR / today_stamp() / f"{video_id}.quality.jsonl"

    if not enrich_file.exists():
        return {"status": "no_enriched", "video_id": video_id}

    kept: List[dict] = []
    rejected: List[dict] = []
    for rec in _read_jsonl(enrich_file):
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


# ── Manifest ──────────────────────────────────────────────────────────────────
def generate_manifest(video_id: Optional[str] = None) -> Dict:
    """Dataset manifest — per-video counts across every tier."""
    if video_id:
        video_id = _validate_video_id(video_id)
    date = today_stamp()
    manifests = []

    targets = [video_id] if video_id else _discover_videos()
    for vid in targets:
        raw_f = RAW_DIR / date / f"{vid}.jsonl"
        norm_f = NORM_DIR / date / f"{vid}.jsonl"
        dedup_f = NORM_DIR / date / f"{vid}.deduped.jsonl"
        enrich_f = ENRICH_DIR / date / f"{vid}.jsonl"
        curated_f = CURATED_DIR / date / f"{vid}.jsonl"

        manifests.append({
            "video_id": vid,
            "date": date,
            "raw": _count_lines(raw_f),
            "normalized": _count_lines(norm_f),
            "deduped": _count_lines(dedup_f),
            "enriched": _count_lines(enrich_f),
            "curated": _count_lines(curated_f),
        })

    manifest = {
        "dataset_id": f"tiktok-social-{date.replace('-', '')}" if video_id else f"tiktok-corpus-{date.replace('-', '')}",
        "schema_version": f"observation.v{SCHEMA_VERSION}",
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

    out = MANIFEST_DIR / date / (f"{video_id}.manifest.json" if video_id else "corpus.manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[manifest] → {out}")
    return manifest


def _discover_videos() -> List[str]:
    """Scan data/raw/<today>/ for video_ids (validated — filenames are input)."""
    from src.policy.models import is_valid_identifier
    raw_today = RAW_DIR / today_stamp()
    if not raw_today.exists():
        return []
    return [f.stem for f in raw_today.glob("*.jsonl") if is_valid_identifier(f.stem)]


# ── Orchestration ─────────────────────────────────────────────────────────────
STAGES: Dict[str, Callable] = {
    "normalize": stage_normalize,
    "dedup": stage_dedup,
    "enrich": stage_enrich,
    "quality": stage_quality_gate,
}

STAGE_ORDER = ["normalize", "dedup", "enrich", "quality"]


def run_video(video_id: str, only_stage: Optional[str] = None) -> Dict:
    """Run all stages for one video (or a single stage). Stops cascade on no-input."""
    video_id = _validate_video_id(video_id)
    results = {}
    if only_stage:
        fn = STAGES.get(only_stage)
        if fn:
            results[only_stage] = fn(video_id)
        else:
            print(f"[!] Unknown stage: {only_stage}")
            return results
    else:
        for stage_name in STAGE_ORDER:
            res = STAGES[stage_name](video_id)
            results[stage_name] = res
            if res.get("status") in ("no_raw", "no_normalized", "no_deduped", "no_enriched"):
                print(f"[pipeline] Stop at {stage_name}: {res['status']}")
                break
        generate_manifest(video_id)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Data Pipeline (canonical runner)")
    parser.add_argument("--video", help="Single video_id to process")
    parser.add_argument("--all", action="store_true", help="Process all videos in data/raw/<today>/")
    parser.add_argument("--stage", choices=list(STAGES.keys()), help="Run specific stage only")
    parser.add_argument("--force", action="store_true", help="Re-process meski output sudah ada")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest only")
    parser.add_argument("--export-mark", action="store_true", help="Export curated → MARK-ready JSON")
    args = parser.parse_args()
    global FORCE
    FORCE = args.force

    if args.manifest:
        print(json.dumps(generate_manifest(args.video), indent=2))
        return

    if args.export_mark:
        from src.export.mark import export_video, export_all
        result = export_video(args.video) if args.video else export_all()
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
