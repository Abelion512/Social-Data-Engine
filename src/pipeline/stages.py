#!/usr/bin/env python3
"""
Idempotent Stage Runner — orchestrates pipeline stages with resume support.

Setiap stage:
  1. Cek output existence + record count
  2. Lewati jika sudah selesai (idempotent)
  3. Jalankan transformasi
  4. Tulis output + manifest

Stages: collect → raw → normalize → dedup → quality gate → annotation → verification → curated
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Dict, Callable, Any

from src.schema.canonical import Observation


class StageRunner:
    """Orchestrate pipeline stages with idempotency."""

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.dirs = {
            "raw": base_dir / "data" / "raw",
            "normalized": base_dir / "data" / "normalized",
            "enriched": base_dir / "data" / "enriched",
            "curated": base_dir / "data" / "curated",
            "rejected": base_dir / "data" / "rejected",
            "manifests": base_dir / "data" / "manifests",
        }
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)

    def run_stage(
        self,
        stage_name: str,
        input_key: str,
        output_key: str,
        transform: Callable[[List[Observation]], List[Observation]],
        manifest: Optional[Dict] = None,
    ) -> List[Observation]:
        """
        Jalankan stage dengan idempotency check.

        Args:
            stage_name: nama stage (untuk logging)
            input_key: key di self.dirs untuk input
            output_key: key di self.dirs untuk output
            transform: fungsi transform input → output
            manifest: optional manifest metadata

        Returns:
            List[Observation] hasil stage
        """
        input_dir = self.dirs[input_key]
        output_dir = self.dirs[output_key]

        # Cek apakah stage sudah selesai
        if self._is_complete(stage_name, input_dir, output_dir):
            print(f"[{stage_name}] Skip — already complete")
            return self._load_output(output_dir)

        # Jalankan transformasi
        print(f"[{stage_name}] Running...")
        start = time.time()
        input_records = self._load_input(input_dir)
        output_records = transform(input_records)

        # Tulis output
        self._write_output(output_dir, output_records)

        # Update manifest
        self._write_manifest(stage_name, {
            "stage": stage_name,
            "input_count": len(input_records),
            "output_count": len(output_records),
            "duration_seconds": round(time.time() - start, 3),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **(manifest or {})
        })

        print(f"[{stage_name}] Done: {len(input_records)} → {len(output_records)} records")
        return output_records

    def _is_complete(self, stage: str, input_dir: Path, output_dir: Path) -> bool:
        """Cek apakah stage sudah selesai berdasarkan manifest."""
        manifest_file = self.dirs["manifests"] / f"{stage}.json"
        if not manifest_file.exists():
            return False

        try:
            with manifest_file.open("r") as f:
                m = json.load(f)
            return m.get("output_count", 0) > 0 and output_dir.exists()
        except (json.JSONDecodeError, KeyError):
            return False

    def _load_input(self, directory: Path) -> List[Observation]:
        """Load JSONL records dari directory."""
        records = []
        for f in sorted(directory.glob("*.jsonl")):
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    records.append(self._dict_to_observation(rec))
        return records

    def _load_output(self, directory: Path) -> List[Observation]:
        """Load output records (same format as input)."""
        return self._load_input(directory)

    def _write_output(self, directory: Path, records: List[Observation]) -> None:
        """Tulis records ke JSONL."""
        for i, rec in enumerate(records):
            out_file = directory / f"{i:06d}.jsonl"
            with out_file.open("w", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def _write_manifest(self, stage: str, data: Dict) -> None:
        """Tulis stage manifest."""
        manifest_file = self.dirs["manifests"] / f"{stage}.json"
        with manifest_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _dict_to_observation(self, d: Dict) -> Observation:
        """Konversi dict → Observation (round-trip dari to_dict)."""
        if isinstance(d, Observation):
            return d
        return Observation.from_dict(d)