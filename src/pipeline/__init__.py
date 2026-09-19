"""
Pipeline package — aggregate layer.

One stage implementation: ``src.pipeline.canonical_runner`` (normalize → dedup
→ enrich → quality gate) wires the modular tiers (dedup/quality) into the CLI
surface. The old inline stages in ``legacy.py`` are gone — ``legacy`` is a thin
re-export shim kept for the documented CLI (``python -m src.pipeline.legacy``)
and for callers importing ``pipeline.legacy`` symbols.

Every canonical_runner symbol is also reachable as ``pipeline.<name>`` via the
PEP 562 ``__getattr__`` below, so ``pipeline.run_video`` /
``pipeline.RAW_DIR`` keep working.
"""
from __future__ import annotations

from typing import Any

from src.pipeline import dedup, quality, identity, stages, improve  # noqa: F401
from src.pipeline import thread_builder  # noqa: F401
from src.pipeline import canonical_runner  # noqa: F401
from src.pipeline import legacy  # noqa: F401  (compat shim → canonical_runner)

__all__ = [
    "legacy", "canonical_runner", "dedup", "quality", "identity", "stages",
    "improve", "thread_builder",
]


def __getattr__(name: str) -> Any:
    """PEP 562 fallback: every canonical_runner symbol is reachable as pipeline.X."""
    try:
        return getattr(canonical_runner, name)
    except AttributeError:
        raise AttributeError(
            f"module 'src.pipeline' has no attribute {name!r} "
            f"(neither in the package nor in src.pipeline.canonical_runner)"
        ) from None
