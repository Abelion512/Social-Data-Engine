"""
Pipeline package — aggregate layer.

Re-exports BOTH:
  * legacy `src/pipeline.py` single-file pipeline (RAW_DIR, CURATED_DIR,
    run_video, STAGES, today_stamp, PIPELINE_VERSION ...) — loaded explicitly
    via importlib so it keeps working even though this package *shadows*
    that file (callers like `tiktok_linkedin.py` do `from src import pipeline`
    and expect `pipeline.RAW_DIR` / `pipeline.run_video` to exist).
  * the modular sub-package (dedup, quality, identity, stages, improve).

No legacy code was modified — legacy is loaded read-only & re-exported.
"""
from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

# ── 1. Load (shadowed) legacy file src/pipeline.py without touching it ─────────
_LEGACY_FILE = _Path(__file__).resolve().parents[1] / "pipeline.py"  # src/pipeline.py (sibling of this package dir)
if _LEGACY_FILE.exists():
    _spec = _ilu.spec_from_file_location("socialdataengine._pipeline_legacy", _LEGACY_FILE)
    _legacy = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
    _sys.modules["socialdataengine._pipeline_legacy"] = _legacy
    _spec.loader.exec_module(_legacy)  # type: ignore[union-attr]

    # re-export legacy public symbols via getattr (avoids import-name
    # resolution over the synthetic module namespace).
    DATA_DIR = _legacy.DATA_DIR          # type: ignore[attr-defined]
    RAW_DIR = _legacy.RAW_DIR
    NORM_DIR = _legacy.NORM_DIR
    ENRICH_DIR = _legacy.ENRICH_DIR
    CURATED_DIR = _legacy.CURATED_DIR
    REJECTED_DIR = _legacy.REJECTED_DIR
    MANIFEST_DIR = _legacy.MANIFEST_DIR
    _ROOT = _legacy._ROOT                # noqa: F811
    today_stamp = _legacy.today_stamp
    PIPELINE_VERSION = _legacy.PIPELINE_VERSION
    STAGES = _legacy.STAGES
    FORCE = _legacy.FORCE
    stage_normalize = _legacy.stage_normalize
    stage_dedup = _legacy.stage_dedup
    stage_enrich = _legacy.stage_enrich
    stage_quality_gate = _legacy.stage_quality_gate
    generate_manifest = _legacy.generate_manifest
    run_video = _legacy.run_video
    _discover_videos = _legacy._discover_videos
else:
    # legacy file removed — define fallback so package still imports
    PIPELINE_VERSION = "1.0.0"

# ── 2. Modular sub-package (refactor layer: stages, dedup, quality, ...) ────────
from src.pipeline import dedup  # noqa: E402
from src.pipeline import quality  # noqa: E402
from src.pipeline import identity  # noqa: E402
from src.pipeline import stages  # noqa: E402
from src.pipeline import improve  # noqa: E402

__all__ = [
    # legacy re-exports
    "DATA_DIR", "RAW_DIR", "NORM_DIR", "ENRICH_DIR", "CURATED_DIR",
    "REJECTED_DIR", "MANIFEST_DIR", "_ROOT", "today_stamp", "PIPELINE_VERSION",
    "STAGES", "FORCE", "stage_normalize", "stage_dedup",
    "stage_enrich", "stage_quality_gate", "generate_manifest", "run_video",
    "_discover_videos",
    # modular
    "dedup", "quality", "identity", "stages", "improve",
]
