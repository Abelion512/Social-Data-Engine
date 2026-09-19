#!/usr/bin/env python3
"""
Identity resolution — one Entity per (provider, author) across observations.

Entity keys are provider-scoped (``<provider>:<author_id>``), so the same person
on two providers stays two Entities until a cross-provider matcher exists.

ponytail: name-similarity matching was removed (no caller, no test — see
docs/PONYTAIL.md "deferred"); re-add it together with the second real provider
(Phase 5), where a cross-provider pair can actually occur.
"""
from __future__ import annotations

from typing import Dict, List, Optional

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
