#!/usr/bin/env python3
"""
TikTok Data Pipeline — Canonical Schema Definitions

Provider-agnostic data model: Observation, Entity, Content, Relationship,
Annotation, Evidence, Provenance, Confidence.

Every level must carry provenance: collector version, pipeline version,
source, timestamps, model, processed_at. This jaga traceability kalau
pipeline berhenti/rollback perlu tahu dari mana data berasal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone


def _now() -> str:
    """ISO-8601 timestamp UTC, konsisten di semua level."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Provenance:
    """Provenance traceability — required at every level."""
    collector_version: str
    pipeline_version: str
    source: str
    captured_at: str = ""
    processed_at: str = field(default_factory=_now)
    model: str = ""
    annotation_version: str = ""


@dataclass
class Confidence:
    """Confidence assessment for entity resolution / annotation."""
    value: float  # 0.0–1.0
    evidence: List[str] = field(default_factory=list)
    method: str = "exact_match"  # exact | heuristic | llm | entity_resolution


@dataclass
class Content:
    """Observation content — text_raw + text_normalized always coexist."""
    text_raw: str
    text_normalized: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class Entity:
    """Cross-platform entity with provider_ids for identity resolution."""
    entity_id: str
    entity_type: str  # user | video | creator | topic
    provider_ids: dict = field(default_factory=dict)  # {"tiktok": "123", "linkedin": "456"}
    display_name: str = ""
    confidence: Optional[Confidence] = None


@dataclass
class Relationship:
    """Relationship between entities."""
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str  # authored | replied_to | mentioned | same_as
    confidence: Optional[Confidence] = None
    evidence: List[str] = field(default_factory=list)


@dataclass
class Annotation:
    """Arbitrary LLM annotation attached to an observation."""
    annotation_id: str
    annotation_type: str  # identity | quality | sentiment | topic
    value: dict
    model: str = ""
    confidence: Optional[Confidence] = None


@dataclass
class Evidence:
    """Evidence trace for a claim / annotation."""
    evidence_id: str
    source: str
    source_ref: str  # URL, comment_id, video_id, etc.
    captured_at: str = ""
    content_snippet: str = ""


@dataclass
class Observation:
    """Top-level observation — ready for RAG / gold set / curated export."""
    observation_id: str
    source: str
    content: Content
    entity_id: Optional[str] = None
    relationships: List[Relationship] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    provenance: Provenance = field(default_factory=lambda: Provenance(
        collector_version="", pipeline_version="", source=""
    ))
    confidence: Optional[Confidence] = None
    schema_version: str = "observation.v1"

    def to_dict(self) -> dict:
        """Serialize to dict for JSONL export."""
        d = {
            "observation_id": self.observation_id,
            "source": self.source,
            "content": {
                "text_raw": self.content.text_raw,
                "text_normalized": self.content.text_normalized,
                "metadata": self.content.metadata,
            },
            "entity_id": self.entity_id,
            "provenance": {
                "collector_version": self.provenance.collector_version,
                "pipeline_version": self.provenance.pipeline_version,
                "source": self.provenance.source,
                "captured_at": self.provenance.captured_at,
                "processed_at": self.provenance.processed_at,
                "model": self.provenance.model,
                "annotation_version": self.provenance.annotation_version,
            },
            "schema_version": self.schema_version,
        }
        if self.entity_id is not None:
            d["entity_id"] = self.entity_id
        if self.confidence is not None:
            d["confidence"] = {
                "value": self.confidence.value,
                "method": self.confidence.method,
                "evidence": self.confidence.evidence,
            }
        if self.content.text_normalized:
            d["content"]["text_normalized"] = self.content.text_normalized
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        """Deserialize from dict (reverse of ``to_dict``).

        Used by :class:`src.pipeline.stages.StageRunner` for idempotent
        JSONL round-trip (load_input → load_output → write_output).
        Tolerates missing/legacy fields gracefully.
        """
        # content ------------------------------------------------------------------
        content = d.get("content", {})
        if not isinstance(content, Content):
            content = Content(
                text_raw=content.get("text_raw", ""),
                text_normalized=content.get("text_normalized", ""),
                metadata=content.get("metadata", {}),
            )

        # provenance ---------------------------------------------------------------
        prov = d.get("provenance", {})
        if not isinstance(prov, Provenance):
            src = prov.get("source") or d.get("source", "")
            prov = Provenance(
                collector_version=prov.get("collector_version", ""),
                pipeline_version=prov.get("pipeline_version", ""),
                source=src,
                captured_at=prov.get("captured_at", ""),
                processed_at=prov.get("processed_at", ""),
                model=prov.get("model", ""),
                annotation_version=prov.get("annotation_version", ""),
            )

        # confidence (optional) ----------------------------------------------------
        conf = d.get("confidence")
        if conf and not isinstance(conf, Confidence):
            conf = Confidence(
                value=conf.get("value", 0.0),
                evidence=conf.get("evidence", []),
                method=conf.get("method", "exact_match"),
            )

        return cls(
            observation_id=d.get("observation_id", ""),
            source=d.get("source", ""),
            content=content,
            entity_id=d.get("entity_id"),
            provenance=prov,
            confidence=conf,
            schema_version=d.get("schema_version", "observation.v1"),
        )


def observation_from_raw(raw: object, source: str = "tiktok") -> Observation:
    """Convenience factory from RawComment or dict for quick pipelines."""
    if hasattr(raw, "comment_id"):
        # RawComment object
        author = raw.author
        return Observation(
            observation_id=f"tiktok:{raw.comment_id}",
            source=source,
            content=Content(
                text_raw=raw.text_raw,
                text_normalized=raw.text_normalized if hasattr(raw, "text_normalized") else "",
                metadata={
                    "video_id": raw.video_id,
                    "comment_id": raw.comment_id,
                    "capture_method": raw.capture_method,
                    "collector_version": raw.collector_version,
                    "likes": raw.likes,
                    "reply_count": raw.reply_count,
                    "create_time": raw.create_time,
                    "parent_comment_id": raw.parent_comment_id,
                    "images": raw.images,
                    "video_context": raw.video_context,
                },
            ),
            entity_id=f"tiktok:{author.author_id}" if author.author_id else f"tiktok:handle:{author.author_handle}",
            provenance=Provenance(
                collector_version=raw.collector_version,
                pipeline_version="1.0.0",
                source=source,
                captured_at=raw.captured_at,
            ),
            schema_version="observation.v1",
        )
    if isinstance(raw, dict):
        # Dict from JSONL
        return Observation(
            observation_id=raw.get("observation_id", ""),
            source=source,
            content=Content(
                text_raw=raw.get("content", {}).get("text_raw", ""),
                text_normalized=raw.get("content", {}).get("text_normalized", ""),
                metadata=raw.get("content", {}).get("metadata", {}),
            ),
            entity_id=raw.get("entity_id"),
            provenance=Provenance(
                collector_version=raw.get("provenance", {}).get("collector_version", ""),
                pipeline_version=raw.get("provenance", {}).get("pipeline_version", ""),
                source=source,
                captured_at=raw.get("provenance", {}).get("captured_at", ""),
            ),
            schema_version=raw.get("schema_version", "observation.v1"),
        )
    raise ValueError(f"Cannot create Observation from {type(raw)}")