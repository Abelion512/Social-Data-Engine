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

from src.schema.canonical import Observation


def build_manifest(base_dir: Path) -> Dict[str, Any]:
    """
    Build manifest lengkap dari seluruh data curated.

    Returns dict dengan ringkasan semua video + stats.
    """
    curated_dir = base_dir / "data" / "curated"
    manifests_dir = base_dir / "data" / "manifests"

    if not curated_dir.exists():
        return {
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

    for f in sorted(curated_dir.glob("*.jsonl")):
        video_id = f.stem
        comments: List[Dict] = []
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                comments.append(rec)

        # Read manifest per-video if exists
        video_manifest_file = manifests_dir / f"{video_id}.json"
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

    # Tulis per-video manifest files
    for video_entry in manifest.get("videos", []):
        vf = manifests_dir / f"{video_entry['video_id']}.json"
        # Cuma tulis field manifest saja
        vm_data = video_entry.get("manifest", {})
        if vm_data:
            vf.write_text(json.dumps(vm_data, indent=2, ensure_ascii=False))