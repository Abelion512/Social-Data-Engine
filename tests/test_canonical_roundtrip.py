#!/usr/bin/env python3
"""
Canonical schema persistence regression tests — self-contained, no pytest.

Guards two defects found in review (both about data surviving a
persist → load cycle through the canonical JSONL layer):

1. ``Observation.to_dict()`` / ``from_dict()`` silently dropped the derived
   layers that hang off an observation — ``relationships``, ``annotations``,
   ``evidence``. ``src/schema/mapper.py`` attaches annotations and
   ``src/pipeline/identity.py`` reads them back, so every write flattened them
   away. Also proven: mapper output lost its ``video_context`` annotation.
2. ``mapper.tiktok_to_canonical()`` published no identity key that
   ``pipeline.identity.resolve_identity()`` consumes (annotation type
   ``author`` / ``metadata["author_id"]``), so cross-platform identity
   resolution returned an empty entity map for its only real producer.

Serialization stays additive: observations with no derived layers keep their
exact legacy key set, so previously written JSONL remains readable.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.identity import resolve_identity
from src.schema.canonical import (
    Annotation,
    Confidence,
    Content,
    Evidence,
    Observation,
    Relationship,
    observation_from_raw,
)
from src.schema.mapper import tiktok_to_canonical
from src.tiktok_schema import Author, RawComment

# Key set emitted for an observation with no confidence and no derived layers
# (legacy shape — ``confidence`` is added only when it is not None).
LEGACY_KEYS = (
    "observation_id", "source", "content", "entity_id", "provenance",
    "schema_version",
)


def _raw(author_id: str = "99887766", handle: str = "@budi") -> RawComment:
    return RawComment(
        video_id="7673343206544706837",
        video_url="https://www.tiktok.com/@budi/video/7673343206544706837",
        comment_id="c1",
        author=Author(author_id, handle, "Budi Santoso"),
        text_raw="let us connect  mahasiswi HI UMY",
        capture_method="api",
        video_context={"caption": "hi", "hashtags": [], "creator": "@budi"},
    )


def test_annotations_survive_roundtrip():
    obs = Observation(
        observation_id="tiktok:1",
        source="tiktok",
        content=Content(text_raw="hello", text_normalized="hello"),
        annotations=[
            Annotation(
                annotation_id="a1",
                annotation_type="author",
                value={"author_id": "42"},
                model="llm-x",
                confidence=Confidence(value=0.9, evidence=["e1"], method="llm"),
            )
        ],
    )
    back = Observation.from_dict(obs.to_dict())
    assert len(back.annotations) == 1, f"annotation lost: {back.annotations}"
    ann = back.annotations[0]
    assert ann.annotation_type == "author"
    assert ann.value == {"author_id": "42"}, ann.value
    assert ann.model == "llm-x"
    assert ann.confidence is not None and ann.confidence.value == 0.9
    assert ann.confidence.evidence == ["e1"]
    assert ann.confidence.method == "llm"
    print("PASS: test_annotations_survive_roundtrip")


def test_relationships_and_evidence_survive_roundtrip():
    obs = Observation(
        observation_id="tiktok:2",
        source="tiktok",
        content=Content(text_raw="reply", text_normalized="reply"),
        relationships=[
            Relationship(
                relationship_id="r1",
                source_entity_id="tiktok:2",
                target_entity_id="tiktok:1",
                relationship_type="replied_to",
                confidence=Confidence(value=0.5, method="heuristic"),
                evidence=["parent_comment_id"],
            )
        ],
        evidence=[
            Evidence(
                evidence_id="ev1",
                source="tiktok",
                source_ref="https://www.tiktok.com/@budi/video/1",
                captured_at="2026-01-01T00:00:00+00:00",
                content_snippet="reply",
            )
        ],
    )
    back = Observation.from_dict(obs.to_dict())
    assert len(back.relationships) == 1, "relationship lost"
    rel = back.relationships[0]
    assert (rel.source_entity_id, rel.target_entity_id) == ("tiktok:2", "tiktok:1")
    assert rel.relationship_type == "replied_to"
    assert rel.evidence == ["parent_comment_id"]
    assert rel.confidence is not None and rel.confidence.method == "heuristic"
    assert len(back.evidence) == 1, "evidence lost"
    assert back.evidence[0].source_ref.endswith("/video/1")
    assert back.evidence[0].content_snippet == "reply"
    print("PASS: test_relationships_and_evidence_survive_roundtrip")


def test_empty_derived_layers_keep_legacy_key_set():
    """Additive-only change: no derived layer present ⇒ no new keys emitted."""
    obs = Observation(
        observation_id="tiktok:3",
        source="tiktok",
        content=Content(text_raw="plain", text_normalized="plain"),
    )
    d = obs.to_dict()
    for key in ("relationships", "annotations", "evidence"):
        assert key not in d, f"legacy shape changed — unexpected key {key!r}"
    assert set(d) == set(LEGACY_KEYS), f"legacy key set changed: {sorted(d)}"
    print("PASS: test_empty_derived_layers_keep_legacy_key_set")


def test_legacy_payload_without_derived_keys_still_loads():
    legacy = {
        "observation_id": "tiktok:4",
        "source": "tiktok",
        "content": {"text_raw": "old", "text_normalized": "old", "metadata": {}},
        "provenance": {"collector_version": "1.1.0", "pipeline_version": "1.1.0",
                       "source": "tiktok"},
        "schema_version": "observation.v1",
    }
    back = Observation.from_dict(legacy)
    assert back.observation_id == "tiktok:4"
    assert back.annotations == [] and back.relationships == [] and back.evidence == []
    print("PASS: test_legacy_payload_without_derived_keys_still_loads")


def test_mapper_identity_resolves_after_persist_load():
    """The full documented chain: mapper → JSONL dict → Observation → Entity."""
    obs = tiktok_to_canonical(_raw())
    assert "author_id" in obs.content.metadata, "mapper dropped metadata author_id"

    back = Observation.from_dict(obs.to_dict())
    types = sorted(a.annotation_type for a in back.annotations)
    assert types == ["author", "video_context"], f"annotations lost: {types}"

    in_memory = resolve_identity([obs])
    after_reload = resolve_identity([back])
    assert list(in_memory) == ["tiktok:99887766"], f"in-memory: {list(in_memory)}"
    assert list(after_reload) == ["tiktok:99887766"], f"after reload: {list(after_reload)}"
    entity = after_reload["tiktok:99887766"]
    assert entity.entity_type == "user"
    assert entity.provider_ids == {"tiktok": "99887766"}
    assert entity.display_name == "Budi Santoso"
    print("PASS: test_mapper_identity_resolves_after_persist_load")


def test_mapper_identity_falls_back_to_handle_without_numeric_id():
    """DOM capture has no numeric author id — the handle path must still work."""
    raw = _raw(author_id="", handle="@coretanmalam2000")
    obs = tiktok_to_canonical(raw)
    back = Observation.from_dict(obs.to_dict())
    assert back.entity_id == "tiktok:handle:@coretanmalam2000", back.entity_id
    assert "author_id" in back.content.metadata
    print("PASS: test_mapper_identity_falls_back_to_handle_without_numeric_id")


def test_observation_from_raw_resolves_identity_and_roundtrips():
    """The second canonical producer (`observation_from_raw`) must carry the
    same identity key, or `resolve_identity` silently returns nothing for it."""
    raw = _raw()
    obs = observation_from_raw(raw)
    assert obs.content.metadata["author_id"] == "99887766", obs.content.metadata

    in_memory = resolve_identity([obs])
    after_reload = resolve_identity([Observation.from_dict(obs.to_dict())])
    assert list(in_memory) == ["tiktok:99887766"], f"in-memory: {list(in_memory)}"
    assert list(after_reload) == ["tiktok:99887766"], f"after reload: {list(after_reload)}"
    assert after_reload["tiktok:99887766"].display_name == "Budi Santoso"
    print("PASS: test_observation_from_raw_resolves_identity_and_roundtrips")


def test_annotation_confidence_absent_stays_absent():
    obs = Observation(
        observation_id="tiktok:5",
        source="tiktok",
        content=Content(text_raw="x"),
        annotations=[Annotation(annotation_id="a", annotation_type="quality",
                                value={"score": 1})],
    )
    d = obs.to_dict()
    assert "confidence" not in d["annotations"][0], "fabricated confidence"
    assert Observation.from_dict(d).annotations[0].confidence is None
    print("PASS: test_annotation_confidence_absent_stays_absent")


TESTS = (
    test_annotations_survive_roundtrip,
    test_relationships_and_evidence_survive_roundtrip,
    test_empty_derived_layers_keep_legacy_key_set,
    test_legacy_payload_without_derived_keys_still_loads,
    test_mapper_identity_resolves_after_persist_load,
    test_mapper_identity_falls_back_to_handle_without_numeric_id,
    test_observation_from_raw_resolves_identity_and_roundtrips,
    test_annotation_confidence_absent_stays_absent,
)


if __name__ == "__main__":
    passed = failed = 0
    for fn in TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:  # noqa: BLE001 — report every failure, keep going
            failed += 1
            print(f"FAIL: {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\nCanonical round-trip suite: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
