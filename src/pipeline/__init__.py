"""
Pipeline sub-package — normalize → dedup → quality → identity → stages.
Plus Auto/Recursive Self-Improvement (architectural feedback loop).
"""
from src.pipeline import dedup  # noqa: F401
from src.pipeline import quality  # noqa: F401
from src.pipeline import identity  # noqa: F401
from src.pipeline import stages  # noqa: F401
from src.pipeline import improve  # noqa: F401

__all__ = ["dedup", "quality", "identity", "stages", "improve"]
