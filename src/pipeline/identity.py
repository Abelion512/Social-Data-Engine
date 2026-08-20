#!/usr/bin/env python3
"""
Cross-Platform Identity Resolution — match entities across providers.

Tujuan: saat user TikTok juga muncul di LinkedIn/YouTube,
keduanya di-identifikasi sebagai entity yang sama dengan confidence score.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set
from difflib import SequenceMatcher

from src.schema.canonical import Entity, Observation, Confidence


def resolve_identity(observations: List[Observation]) -> Dict[str, Entity]:
    """
    Build entity map dari observations.

    Returns dict: entity_id → Entity
    """
    entities: Dict[str, Entity] = {}

    for obs in observations:
        provider = obs.source
        author_id = _extract_author_id(obs)

        if not author_id:
            continue

        entity_key = f"{provider}:{author_id}"

        if entity_key not in entities:
            entities[entity_key] = Entity(
                entity_id=entity_key,
                entity_type="user",
                provider_ids={provider: author_id},
                display_name=_extract_display_name(obs),
                confidence=Confidence(
                    value=1.0,
                    method="exact_match",
                    evidence=[f"provider_{provider}_direct"]
                )
            )
        else:
            # Tambah provider_id jika ada provider baru
            entities[entity_key].provider_ids[provider] = author_id

    return entities


def cross_platform_match(
    entities: Dict[str, Entity],
    threshold: float = 0.85
) -> List[tuple[str, str, float]]:
    """
    Cari kemungkinan entity yang sama lintas provider.

    Returns list of (entity_a_id, entity_b_id, confidence)
    """
    matches: List[tuple[str, str, float]] = []
    entity_list = list(entities.values())

    for i, a in enumerate(entity_list):
        for b in entity_list[i + 1:]:
            if a.provider_ids.keys() & b.provider_ids.keys():
                # Provider sama → skip
                continue

            sim = _similarity(a.display_name, b.display_name)
            if sim >= threshold:
                matches.append((a.entity_id, b.entity_id, round(sim, 3)))

    return matches


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_author_id(obs: Observation) -> Optional[str]:
    """Extract author ID dari annotations atau metadata."""
    for ann in obs.annotations:
        if ann.annotation_type == "author":
            return str(ann.value.get("author_id", ""))

    # Fallback: cek content metadata
    meta = obs.content.metadata or {}
    return str(meta.get("author_id", "")) or None


def _extract_display_name(obs: Observation) -> str:
    """Extract display name dari annotations atau metadata."""
    for ann in obs.annotations:
        if ann.annotation_type == "author":
            return str(ann.value.get("display_name", ""))

    meta = obs.content.metadata or {}
    return str(meta.get("display_name", ""))


def _similarity(a: str, b: str) -> float:
    """Normalized similarity score 0-1."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()