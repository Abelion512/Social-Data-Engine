#!/usr/bin/env python3
"""
Shared configuration — 9Router LLM settings.

Digunakan oleh pipeline.py, tiktok_linkedin.py, dan agent lainnya.
Single source of truth untuk .env loading + model chain.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict

_ROOT = Path(__file__).resolve().parent.parent
_SRC = Path(__file__).resolve().parent


def env_load() -> Dict[str, str]:
    """Load .env file dari src/.env. Returns dict of key=value."""
    env: Dict[str, str] = {}
    p = _SRC / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


_ENV = env_load()


def _env(key: str, alt: str = "", default: str = "") -> str:
    """Resolve config: src/.env -> os.environ (alt 9Router name) -> default.

    9Router pakai nama NINEROUTER_URL / NINEROUTER_API_KEY, project .env pakai
    BASE_URL / API_KEY. Support keduanya agar vision-captcha + identity
    enrichment jalan baik via `run.sh` (export env) maupun `src/.env`.
    """
    v = _ENV.get(key, "")
    if v:
        return v
    if alt:
        v = os.environ.get(alt, "")
    return v or os.environ.get(key, default)


# ── 9Router API config ────────────────────────────────────────────────────────
_ninerouter_url = _env("BASE_URL", "NINEROUTER_URL", "http://localhost:20128/v1")
LLM_API: str = _ninerouter_url.rstrip("/") + "/chat/completions"
LLM_KEY: str = _env("API_KEY", "NINEROUTER_API_KEY", "")
LLM_MODEL: str = _env("MODEL_PLANNER", "", "oc/deepseek-v4-flash-free")

# Chain fallback: Gemini (cepat) -> planner -> fallback -> claude-work
GEMINI_MODEL: str = _env("MODEL_VISION_DEFAULT", "MODEL_ID", "gc/gemini-3.1-flash-lite")
ENRICH_MODELS: list = [
    GEMINI_MODEL,
    LLM_MODEL,
    _env("MODEL_PLANNER_FALLBACK", "", "ac/deepseek-v4-flash"),
    "claude-work",
]

# Vision
VISION_MODEL: str = GEMINI_MODEL
VISION_MODEL_FALLBACK: str = _env("MODEL_VISION_OCR", "MODEL_ID", "oc/mimo-v2.5-free")
