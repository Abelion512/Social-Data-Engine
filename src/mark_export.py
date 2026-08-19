#!/usr/bin/env python3
"""
MARK Agent Export — Konversi curated pipeline output ke format MARK.

MARK agent (Mazees/mark-agent) punya:
  - Orama (hybrid vector + full-text search)
  - Transformers.js (local embeddings)
  - /social-knowledge path

Output format:
  data/mark/<video_id>.json — siap di-import ke MARK's knowledge base
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timezone

_ROOT = Path(__file__).resolve().parent.parent


def export_video(video_id: str, date: str = None) -> Dict:
    """
    Export satu video curated → MARK-ready JSON.

    Returns summary dict.
    """
    if not date:
        date = time.strftime("%Y-%m-%d")

    curated_file = _ROOT / "data" / "curated" / date / f"{video_id}.jsonl"
    if not curated_file.exists():
        return {"status": "no_curated", "video_id": video_id}

    comments: List[Dict] = []
    with curated_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            comments.append(_to_mark_format(rec))

    if not comments:
        return {"status": "empty", "video_id": video_id}

    # Video context dari komentar pertama
    first = comments[0]
    video_ctx = first.get("video_context", {})

    mark_output = {
        "source": "tiktok",
        "video_id": video_id,
        "video_context": {
            "caption": video_ctx.get("caption", ""),
            "hashtags": video_ctx.get("hashtags", []),
            "creator": video_ctx.get("creator", ""),
        },
        "comments": comments,
        "stats": {
            "total": len(comments),
            "with_identity": sum(1 for c in comments if c.get("identity")),
            "avg_quality": round(
                sum(c.get("quality_score", 0) for c in comments) / max(len(comments), 1),
                4,
            ),
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    # Tulis ke data/mark/
    mark_dir = _ROOT / "data" / "mark"
    mark_dir.mkdir(parents=True, exist_ok=True)
    out_path = mark_dir / f"{video_id}.json"
    out_path.write_text(json.dumps(mark_output, indent=2, ensure_ascii=False))

    print(f"[mark-export] {video_id}: {len(comments)} comments → {out_path}")
    return {"status": "ok", "video_id": video_id, "output": str(out_path), "count": len(comments)}


def export_all(date: str = None) -> Dict:
    """Export semua video curated hari ini → satu corpus."""
    if not date:
        date = time.strftime("%Y-%m-%d")

    curated_dir = _ROOT / "data" / "curated" / date
    if not curated_dir.exists():
        return {"status": "no_curated_dir", "date": date}

    all_comments: List[Dict] = []
    video_ids: List[str] = []

    for f in sorted(curated_dir.glob("*.jsonl")):
        vid = f.stem
        video_ids.append(vid)
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                all_comments.append(_to_mark_format(rec))

    if not all_comments:
        return {"status": "empty", "date": date}

    corpus = {
        "source": "tiktok",
        "type": "corpus",
        "date": date,
        "video_ids": video_ids,
        "comments": all_comments,
        "stats": {
            "total": len(all_comments),
            "video_count": len(video_ids),
            "with_identity": sum(1 for c in all_comments if c.get("identity")),
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    mark_dir = _ROOT / "data" / "mark"
    mark_dir.mkdir(parents=True, exist_ok=True)
    out_path = mark_dir / f"corpus-{date}.json"
    out_path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False))

    print(f"[mark-export] corpus: {len(all_comments)} comments dari {len(video_ids)} video → {out_path}")
    return {"status": "ok", "output": str(out_path), "count": len(all_comments)}


def _to_mark_format(rec: dict) -> dict:
    """Konversi enriched record ke format minimal untuk MARK."""
    return {
        "id": rec.get("comment_id", ""),
        "text": rec.get("text_normalized", rec.get("text_raw", "")),
        "text_original": rec.get("text_raw", ""),
        "author": rec.get("author_handle", ""),
        "display_name": rec.get("display_name", ""),
        "likes": rec.get("likes", 0),
        "quality_score": rec.get("quality", {}).get("curated_score", 0),
        "identity": rec.get("identity"),
        "video_context": rec.get("video_context", {}),
        "parent_comment_id": rec.get("parent_comment_id", ""),
        "images": rec.get("images", []),
    }
