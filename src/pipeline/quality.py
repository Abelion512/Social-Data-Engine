#!/usr/bin/env python3
"""
Quality Scoring & Gating — heuristic filtering sebelum LLM enrichment.

Tujuan: batasi panggilan LLM hanya ke komentar yang bernilai (quality > threshold)
dan lolos spam/toxicity gate. Pipeline hemat token.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from src.schema.canonical import Observation


# ── Quality thresholds (mirip pipeline/legacy.py) ────────────────────────────
QUALITY_MIN = 0.30      # curated_score minimum untuk curated set
SEMANTIC_DENSITY_MIN = 0.15
SPAM_MAX = 0.85
TOXIC_MAX = 0.80
GATING_THRESHOLD = 0.15  # quality_score minimum untuk panggil LLM (hemat token)

_URL_RE = re.compile(r'https?://\S+|www\.\S+')
_TOXIC_RE = re.compile(r'\b(bitch|slut|kill yourself)\b', re.I)
_REPEAT_RE = re.compile(r'(.)\1{3,}')


def compute_quality_score(obs: Observation) -> Dict[str, float]:
    """
    Hitung skor quality heuristik (heuristic scoring)

    Args:
        obs: canonical Observation (or a raw record duck-typed via ``text=``)

    Returns dict dengan keys:
      - quality: 0-1, semakin tinggi semakin bagus
      - semantic_density: unique words / total words
      - spam_probability: 0-1, URL + repetisi
      - toxicity: 0-1, kata toksik
      - curated_score: weighted combo
    """
    # Flex entry: canonical Observation (attr) or flat legacy record dict (key).
    # Both callers run the same scoring — single implementation, no drift.
    if isinstance(obs, dict):
        rec = obs
        text = rec.get("text_normalized") or rec.get("text_raw") or ""
    else:
        rec = None
        text = obs.content.text_raw or ""
    if not text.strip():
        return {
            "quality": 0.0,
            "curated_score": 0.0,
            "semantic_density": 0.0,
            "spam_probability": 1.0,
            "toxicity": 0.0,
        }

    words = text.split()
    unique_ratio = len(set(words)) / max(len(words), 1)

    url_count = len(_URL_RE.findall(text))
    toxic_hit = bool(_TOXIC_RE.search(text))
    toxicity = 1.0 if toxic_hit else (0.8 if url_count > 2 else 0.0)

    # Spam signals (legacy semantics): repeated chars, emoji bursts, digit
    # floods, link bursts. >1 URL = near-certain spam (flat 0.95).
    repeat_chars = len(_REPEAT_RE.findall(text))
    emoji_count = sum(1 for c in text if ord(c) > 0x2700)
    digit_count = sum(c.isdigit() for c in text)
    if url_count > 1:
        spam_prob = 0.95
    else:
        spam_signals = repeat_chars + emoji_count * 0.1 + (digit_count / max(len(text), 1))
        spam_prob = min(spam_signals / 5.0, 0.95)

    # Score = mean(density, non-spam, non-toxic) × (1-spam) × (1-toxic) —
    # the legacy composite: each axis removes its own share, so a spammy or
    # toxic record cannot survive on semantic density alone.
    toxicity_score = min(0.9, 0.9 if toxic_hit else (0.8 if url_count > 2 else 0.0))
    quality = (unique_ratio + (1 - spam_prob) + (1 - toxicity_score)) / 3.0
    curated = quality * (1 - spam_prob) * (1 - toxicity_score)

    return {
        "quality": round(curated, 4),
        "semantic_density": round(unique_ratio, 3),
        "spam_probability": round(spam_prob, 3),
        "toxicity": round(toxicity_score, 3),
        "curated_score": round(curated, 4),
    }


def passes_gate(obs: Observation) -> bool:
    """
    Cek apakah observation lolos quality gate untuk masuk curated set.
    """
    score = compute_quality_score(obs)
    return (
        score["curated_score"] >= QUALITY_MIN
        and score["spam_probability"] <= SPAM_MAX
        and score["toxicity"] <= TOXIC_MAX
    )


def passes_llm_gate(obs: Observation) -> bool:
    """
    Cek apakah observation layak dikirim ke LLM enrichment.
    Lebih longgar dari curated gate — hanya filter spam/empty.
    """
    score = compute_quality_score(obs)
    return score["quality"] >= GATING_THRESHOLD and score["spam_probability"] < 0.9