#!/usr/bin/env python3
"""
Quality Scoring & Gating — heuristic filtering sebelum LLM enrichment.

Tujuan: batasi panggilan LLM hanya ke komentar yang bernilai (quality > threshold)
dan lolos spam/toxicity gate. Pipeline hemat token.
"""
from __future__ import annotations

import re
from typing import Dict

from src.schema.canonical import Observation


# ── Quality thresholds (mirip pipeline.py lama) ──────────────────────────────
QUALITY_MIN = 0.30      # curated_score minimum untuk curated set
SEMANTIC_DENSITY_MIN = 0.15
SPAM_MAX = 0.85
TOXIC_MAX = 0.80
GATING_THRESHOLD = 0.15  # quality_score minimum untuk panggil LLM (hemat token)

_URL_RE = re.compile(r'https?://\S+|www\.\S+')
_TOXIC_RE = re.compile(r'\b(bitch|slut|kill yourself)\b', re.I)


def compute_quality_score(obs: Observation) -> Dict[str, float]:
    """
    Hitung skor quality heuristik.

    Returns dict dengan keys:
      - quality: 0-1, semakin tinggi semakin bagus
      - semantic_density: unique words / total words
      - spam_probability: 0-1, URL + repetisi
      - toxicity: 0-1, kata toksik
      - curated_score: weighted combo
    """
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

    spam_prob = min(1.0, url_count * 0.4 + (1.0 - unique_ratio) * 0.3)
    quality = max(0.0, unique_ratio * 0.6 - spam_prob * 0.3 - toxicity * 0.4)

    return {
        "quality": round(quality, 3),
        "semantic_density": round(unique_ratio, 3),
        "spam_probability": round(spam_prob, 3),
        "toxicity": round(toxicity, 3),
        "curated_score": round(quality, 3),
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