#!/usr/bin/env python3
"""
Export Manifest — metadata dari stage pipeline stages.

Membaca seluruh data curated + manifest stage records,
membuat satu manifest tercentral untuk export/import.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime, timezone

from src.policy.models import is_valid_identifier
from src.runtime.context import require_slug_identifier, rooted_file

# Dataset-manifest schema stamp (FR-PROV-003/004). Additive: a consumer can
# invalidate a cached manifest when this changes instead of guessing from shape.
MANIFEST_SCHEMA_VERSION = "manifest.v1"


def _curated_files(curated_dir: Path) -> List[tuple]:
    """(video_id, path) for every curated file, one entry per video.

    The pipeline writes the dated layout `data/curated/<YYYY-MM-DD>/<id>.jsonl`
    (`src/runtime/context.py::curated_file`, `export/mark.py`, the LinkedIn
    consumer), while an older flat layout put files directly in
    `data/curated/`. Scanning only the flat form made this function return
    `total_videos: 0` for a real corpus — a silent empty success
    (`policies/TRANSPARENCY.md` forbids that), so both are read now: newest date
    first, first hit per video id wins, flat files last. Names that could not be
    a real id are skipped rather than propagated into paths.
    """
    candidates: List[Path] = []
    for date_dir in sorted((d for d in curated_dir.iterdir() if d.is_dir()), reverse=True):
        candidates.extend(sorted(date_dir.glob("*.jsonl")))
    candidates.extend(sorted(curated_dir.glob("*.jsonl")))
    seen: Dict[str, Path] = {}
    for path in candidates:
        video_id = path.stem
        if is_valid_identifier(video_id) and video_id not in seen:
            seen[video_id] = path
    return sorted(seen.items())


def build_manifest(base_dir: Path) -> Dict[str, Any]:
    """
    Build manifest lengkap dari seluruh data curated.

    Returns dict dengan ringkasan semua video + stats.
    """
    curated_dir = base_dir / "data" / "curated"
    manifests_dir = base_dir / "data" / "manifests"

    if not curated_dir.exists():
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source": "unknown",
            "total_videos": 0,
            "total_comments": 0,
            "total_with_identity": 0,
            "videos": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    videos: List[Dict] = []
    total_comments = 0
    total_with_identity = 0

    for video_id, f in _curated_files(curated_dir):
        comments: List[Dict] = []
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                comments.append(rec)

        # Read manifest per-video if exists (rooted — stem already validated)
        video_manifest_file = rooted_file(manifests_dir, f"{video_id}.json",
                                          "per-video manifest (build_manifest)")
        vm = {}
        if video_manifest_file.exists():
            with video_manifest_file.open("r", encoding="utf-8") as vf:
                vm = json.load(vf)

        total_comments += len(comments)
        identity_count = sum(
            1 for c in comments if c.get("identity")
        )
        total_with_identity += identity_count

        videos.append({
            "video_id": video_id,
            "comment_count": len(comments),
            "with_identity": identity_count,
            "manifest": vm,
        })

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": "tiktok",
        "total_videos": len(videos),
        "total_comments": total_comments,
        "total_with_identity": total_with_identity,
        "videos": videos,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_manifest(base_dir: Path, manifest: Dict[str, Any]) -> None:
    """Tulis manifest ke disk."""
    manifests_dir = base_dir / "data" / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    # Tulis manifest utama
    main_file = manifests_dir / "manifest.json"
    with main_file.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Tulis per-video manifest files. `manifest` is caller-supplied data, so each
    # id is validated and its path resolved inside `manifests_dir` (TM-13): a
    # hostile entry must never be able to steer a write out of the data tree.
    for video_entry in manifest.get("videos", []):
        vid = require_slug_identifier(video_entry.get("video_id", ""), "video_id")
        vf = rooted_file(manifests_dir, f"{vid}.json", "per-video manifest (write_manifest)")
        # Cuma tulis field manifest saja
        vm_data = dict(video_entry.get("manifest", {}))
        if vm_data:
            # Stamp the schema once, without overwriting a caller-provided value.
            vm_data.setdefault("schema_version", MANIFEST_SCHEMA_VERSION)
            vf.write_text(json.dumps(vm_data, indent=2, ensure_ascii=False))