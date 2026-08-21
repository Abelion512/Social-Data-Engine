#!/usr/bin/env python3
"""
Schema Mapper — Convert provider-specific data to canonical schema.

TikTok RawComment → canonical Observation, including:
  - Entity resolution (provider-prefixed entity_id)
  - Provenance (collector_version, captured_at, source)
  - Confidence (direct_collection = 1.0)
  - Video context annotation
"""
from __future__ import annotations

from src.schema.canonical import (
    Observation,
    Content,
    Provenance,
    Confidence,
    Annotation,
)
from src.tiktok_schema import RawComment, normalize_text, SCHEMA_VERSION

PIPELINE_VERSION = "1.1.0"


def tiktok_to_canonical(raw: RawComment) -> Observation:
    """
    Convert TikTok RawComment → canonical Observation.

    Preserves text_raw, adds text_normalized, attaches entity_id with
    provider prefix for cross-platform resolution.
    """
    author = raw.author
    has_author_id = bool(author.author_id)
    entity_id = (
        f"tiktok:{author.author_id}"
        if has_author_id
        else f"tiktok:handle:{author.author_handle}"
    )

    content = Content(
        text_raw=raw.text_raw,
        text_normalized=normalize_text(raw.text_raw),
        metadata={
            "video_id": raw.video_id,
            "video_url": raw.video_url,
            "comment_id": raw.comment_id,
            "parent_comment_id": raw.parent_comment_id,
            "capture_method": raw.capture_method,
            "likes": raw.likes,
            "reply_count": raw.reply_count,
            "create_time": raw.create_time,
            "images": raw.images,
            "author_handle": author.author_handle,
            "display_name": author.display_name,
        },
    )

    prov = Provenance(
        collector_version=raw.collector_version,
        pipeline_version=PIPELINE_VERSION,
        source=raw.source,
        captured_at=raw.captured_at,
    )

    obs = Observation(
        observation_id=f"tiktok:{raw.comment_id}",
        source=raw.source,
        content=content,
        entity_id=entity_id,
        provenance=prov,
        confidence=Confidence(
            value=1.0,
            evidence=["direct_collection"],
            method="exact_match",
        ),
        schema_version=f"observation.v{SCHEMA_VERSION}",
    )

    # Video context as annotation — "gue juga" without context = not useful
    obs.annotations.append(
        Annotation(
            annotation_id=f"tiktok:{raw.comment_id}:video_context",
            annotation_type="video_context",
            value=raw.video_context,
        )
    )

    return obs