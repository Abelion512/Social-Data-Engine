#!/usr/bin/env python3
"""
TikTok Data Pipeline — legacy CLI surface (compatibility shim).

Since 2026-09-19 the stage implementation lives in ONE place:
``src/pipeline/canonical_runner.py`` (modular tiers wired in). This module
re-exports its public surface so the documented CLI keeps working (see
``docs/CURRENT-STATE.md``):

  python src/pipeline/legacy.py --video 7472094895228468510   # raw→curated
  python src/pipeline/legacy.py --all                         # semua video di data/raw/
  python src/pipeline/legacy.py --stage normalize --all
  python -m src.pipeline.legacy --video VID --stage quality

History: this was a single file at ``src/pipeline.py`` (later
``src/pipeline/legacy.py``), which duplicated the modular stages — debt §6.8 /
docs/CURRENT-STATE.md §6.8 / docs/PONYTAIL.md §6. That duplicate is gone; the ladder rung is
"reuse what exists" (docs/PONYTAIL.md).
"""
from __future__ import annotations

# Explicit re-exports only — no `import *` (pyflakes cannot verify star-import
# surfaces, and the explicit list below IS the compatibility contract).
from src.pipeline.canonical_runner import (  # noqa: F401  (explicit for IDE/grep)
    CURATED_DIR,
    DATA_DIR,
    ENRICH_DIR,
    FORCE,
    GATING_THRESHOLD,
    LLM_MAX_TIMEOUT,
    LLM_API,
    LLM_KEY,
    LLM_MODEL,
    MANIFEST_DIR,
    NORM_DIR,
    PIPELINE_VERSION,
    QUALITY_MIN,
    RAW_DIR,
    REJECTED_DIR,
    SPAM_MAX,
    STAGES,
    STAGE_ORDER,
    Author,
    RawComment,
    _validate_video_id,
    dedup_all,
    generate_manifest,
    hash_exact,
    hash_normalized,
    llm_enrich_identities,
    normalize_text,
    quality_score,
    run_video,
    stage_dedup,
    stage_enrich,
    stage_normalize,
    stage_quality_gate,
    today_stamp,
    write_jsonl,
)

if __name__ == "__main__":
    from src.pipeline.canonical_runner import main
    main()
