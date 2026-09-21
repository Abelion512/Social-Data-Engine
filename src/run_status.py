#!/usr/bin/env python3
"""
Run status — the read-only provenance view a host (MCP client, plugin, operator)
can ask for instead of inspecting files by hand.

Implements the "external inspectability" item of `policies/TRANSPARENCY.md` and
the manifest/version-stamp part of FR-PROV-003/004, using artifacts the pipeline
**already writes** — nothing new is persisted here:

| Artifact | Written by |
|---|---|
| `state/jobs/<video_id>.json` | `src/collector.py` (checkpoint: status, coverage, termination reason, counts) |
| `state/loops/<video_id>.json` | `src/pipeline/improve.py` (loop position: iteration, outcome) |
| `data/manifests/<video_id>.improve.jsonl` | `src/pipeline/improve.py` (one line per improvement iteration) |
| `data/manifests/<video_id>.json` | `src/export/manifest.py` (per-video manifest) |
| `data/curated/<date>/<video_id>.jsonl` | the pipeline (curated records) |

Rules this module obeys:
  - **fail-closed identifiers** — a caller-supplied `video_id` goes through
    `require_slug_identifier`, every path is built with `rooted_file` (S-G2,
    TM-13), and only ids passing `is_valid_identifier` are scanned off disk.
  - **never a fake empty success** — a missing artifact is reported as the
    explicit `no_run_found` status plus a human-readable note, never as a
    zero-filled record that looks like a completed run
    (`policies/TRANSPARENCY.md`).
  - **bounded reads** — every scan has a cap (runs, lines, bytes) and says so
    when a cap was hit instead of silently truncating.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.policy.models import is_valid_identifier
from src.runtime.context import require_slug_identifier, rooted_file

ROOT = Path(__file__).resolve().parents[1]

RUN_STATUS_SCHEMA_VERSION = "run-status.v1"
MAX_RUNS = 50                 # runs reported in one call
MAX_IMPROVE_LINES = 200       # improve-manifest lines read per run
MAX_MANIFEST_BYTES = 1_000_000
MAX_CURATED_LINES = 200_000   # stop counting a pathological curated file

CHECKPOINT_DIR = ("state", "jobs")
LOOP_DIR = ("state", "loops")
MANIFEST_DIR = ("data", "manifests")
CURATED_DIR = ("data", "curated")

SOURCES = {
    "checkpoints": "state/jobs/<video_id>.json",
    "loop_state": "state/loops/<video_id>.json",
    "improve_manifest": "data/manifests/<video_id>.improve.jsonl",
    "video_manifest": "data/manifests/<video_id>.json",
    "curated": "data/curated/<date>/<video_id>.jsonl",
}


def _artifact(root: Path, base_parts: Tuple[str, ...], video_id: str,
              suffix: str, label: str) -> Path:
    """`<root>/<base>/<video_id><suffix>` — id validated, path rooted (S-G2)."""
    require_slug_identifier(video_id, "video_id")
    return rooted_file(root.joinpath(*base_parts), f"{video_id}{suffix}", label)


def _read_json(path: Path, warnings: List[str]) -> Optional[Dict[str, Any]]:
    """Parse a JSON file defensively; every failure is named, never swallowed."""
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            warnings.append(f"{path.name}: dilewati (>{MAX_MANIFEST_BYTES} byte)")
            return None
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        warnings.append(f"{path.name}: tidak terbaca ({type(exc).__name__}: {exc})")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path.name}: bukan objek JSON")
        return None
    return data


def _improve_iterations(path: Path, warnings: List[str]) -> List[Dict[str, Any]]:
    """Improvement iterations recorded for one video (bounded, torn lines noted)."""
    out: List[Dict[str, Any]] = []
    torn = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= MAX_IMPROVE_LINES:
                    warnings.append(
                        f"{path.name}: hanya {MAX_IMPROVE_LINES} baris pertama dibaca")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    torn += 1
                    continue
                if isinstance(record, dict):
                    out.append({
                        "iteration": record.get("iteration"),
                        "job_id": record.get("job_id"),
                        "actions": record.get("actions"),
                        "rationale": record.get("rationale"),
                        "timestamp": record.get("timestamp"),
                        "policy_model_version": record.get("policy_model_version"),
                    })
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"{path.name}: tidak terbaca ({type(exc).__name__}: {exc})")
        return []
    if torn:
        warnings.append(f"{path.name}: {torn} baris rusak diabaikan (append terpotong)")
    return out


def _count_curated(root: Path, video_id: str, warnings: List[str]) -> Optional[int]:
    """Curated records for a video across dated folders (bounded line count)."""
    curated_root = root.joinpath(*CURATED_DIR)
    if not curated_root.is_dir():
        return None
    total = 0
    found = False
    for date_dir in sorted(curated_root.iterdir(), reverse=True):
        if not date_dir.is_dir() or not is_valid_identifier(date_dir.name):
            continue
        candidate = _artifact(root, CURATED_DIR + (date_dir.name,), video_id, ".jsonl",
                              "curated file")
        if not candidate.is_file():
            continue
        found = True
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                for _ in handle:
                    total += 1
                    if total > MAX_CURATED_LINES:
                        warnings.append(
                            f"curated/{date_dir.name}/{video_id}.jsonl: hitungan "
                            f"dihentikan pada {MAX_CURATED_LINES} baris")
                        return None
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"curated/{date_dir.name}/{video_id}.jsonl: tidak terbaca "
                            f"({type(exc).__name__}: {exc})")
    return total if found else None


def _run_entry(root: Path, video_id: str) -> Dict[str, Any]:
    """Status of ONE video: checkpoint + loop position + provenance artifacts."""
    warnings: List[str] = []
    checkpoint_path = _artifact(root, CHECKPOINT_DIR, video_id, ".json", "job checkpoint")
    loop_path = _artifact(root, LOOP_DIR, video_id, ".json", "loop state")
    manifest_path = _artifact(root, MANIFEST_DIR, video_id, ".json", "video manifest")
    improve_path = _artifact(root, MANIFEST_DIR, video_id, ".improve.jsonl", "improve manifest")

    checkpoint = _read_json(checkpoint_path, warnings)
    loop = _read_json(loop_path, warnings)
    manifest = _read_json(manifest_path, warnings)
    iterations = _improve_iterations(improve_path, warnings)

    if checkpoint is None and loop is None and manifest is None and not iterations:
        # Distinguish "nothing was ever written" from "something is there but
        # unreadable": collapsing the second case into the first would hide a
        # corrupt checkpoint behind an empty result (TRANSPARENCY.md).
        exists = [p.name for p in (checkpoint_path, loop_path, manifest_path, improve_path)
                  if p.exists()]
        return {
            "video_id": video_id,
            "status": "unreadable" if exists else "no_run_found",
            "expected_artifacts": [str(checkpoint_path), str(improve_path)],
            "present_but_unreadable": exists,
            "warnings": warnings,
        }

    checkpoint = checkpoint or {}
    entry: Dict[str, Any] = {
        "video_id": video_id,
        "status": "recorded",
        "job_id": checkpoint.get("job_id") or (loop or {}).get("job_id"),
        "checkpoint_status": checkpoint.get("status"),
        "collection_status": checkpoint.get("collection_status"),
        "termination_reason": checkpoint.get("termination_reason"),
        "coverage": checkpoint.get("coverage"),
        "reported_comment_count": checkpoint.get("reported_comment_count"),
        "captured_comment_count": checkpoint.get("captured_comment_count"),
        "started_at": checkpoint.get("started_at"),
        "ended_at": checkpoint.get("ended_at"),
        "cursor": checkpoint.get("cursor"),
        "curated_records": _count_curated(root, video_id, warnings),
        "loop": ({"next_iteration": (loop or {}).get("next_iteration"),
                  "outcome": (loop or {}).get("outcome"),
                  "updated_at": (loop or {}).get("updated_at")} if loop else None),
        "manifest_schema_version": (manifest or {}).get("schema_version"),
        "manifest_present": manifest is not None,
        "improve_iterations": iterations,
        "improve_iteration_count": len(iterations),
        "warnings": warnings,
    }

    # Consistency check worth surfacing: a finished checkpoint with no curated
    # records means the downstream stage never ran (or wrote somewhere else).
    if entry["checkpoint_status"] == "done" and entry["curated_records"] in (None, 0):
        entry["warnings"].append(
            "checkpoint selesai tetapi tidak ada curated records — pipeline downstream "
            "belum dijalankan atau output ada di tempat lain")
    return entry


def collect_run_status(root: Optional[Path] = None,
                       video_id: Optional[str] = None,
                       max_runs: int = MAX_RUNS) -> Dict[str, Any]:
    """Status for one video (validated id) or a bounded listing of recorded runs."""
    root = Path(root) if root is not None else ROOT
    limit = max(1, min(int(max_runs), MAX_RUNS))
    notes: List[str] = []

    if video_id is not None:
        require_slug_identifier(video_id, "video_id")
        entry = _run_entry(root, video_id)
        if entry["status"] == "no_run_found":
            notes.append(
                f"tidak ada artefak run untuk video_id '{video_id}' di {root} "
                "(checkpoint/manifest/improve semuanya tidak ada)")
        elif entry["status"] == "unreadable":
            notes.append(
                f"artefak untuk video_id '{video_id}' ada tetapi tidak ada yang terbaca: "
                + "; ".join(entry.get("warnings") or ["penyebab tidak dilaporkan"]))
        return {
            "schema_version": RUN_STATUS_SCHEMA_VERSION,
            "video_id": video_id,
            "count": 0 if entry["status"] == "no_run_found" else 1,
            "runs": [] if entry["status"] == "no_run_found" else [entry],
            "run": entry,
            "sources": SOURCES,
            "notes": notes,
        }

    checkpoint_dir = root.joinpath(*CHECKPOINT_DIR)
    ids: List[str] = []
    if checkpoint_dir.is_dir():
        for path in sorted(checkpoint_dir.glob("*.json")):
            if is_valid_identifier(path.stem) and path.stem not in ids:
                ids.append(path.stem)
    truncated = len(ids) > limit
    ids = ids[:limit]

    runs = [_run_entry(root, vid) for vid in ids]
    if not runs:
        notes.append(f"tidak ada checkpoint run di {checkpoint_dir} — belum ada koleksi "
                     "yang tercatat di workspace ini")
    if truncated:
        notes.append(f"hanya {limit} run pertama dilaporkan (naikkan max_runs, maks {MAX_RUNS})")
    return {
        "schema_version": RUN_STATUS_SCHEMA_VERSION,
        "video_id": None,
        "count": len(runs),
        "runs": runs,
        "sources": SOURCES,
        "notes": notes,
    }
