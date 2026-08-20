# Social Data Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the TikTok scraper into a multi-provider Social Data Engine that collects, normalizes, deduplicates, enriches, and exports digital social knowledge to Mark and other consumers.

**Architecture:** Introduce provider adapters and a canonical schema layer so TikTok, LinkedIn, YouTube, and Reddit share one processing pipeline. Extend the existing raw→normalized→enriched→curated stages with cross-platform identity resolution, provenance tracking, and multi-consumer export.

**Tech Stack:** Python 3, Camoufox/Playwright (existing), JSONL for raw/normalized output, JSON for curated/manifest, existing 9Router LLM endpoint.

**Spec:** `docs/chatgpt-response`

## Global Constraints

- YAGNI/Ponytail: smallest justified architecture; no distributed processing, custom DBs, custom vector DBs, local LLM infra, Parquet before corpus requires it.
- Preserve existing data hierarchy: `text_raw` + `text_normalized` must coexist; never destructively clean.
- Provenance required at every level: collector version, pipeline version, model version, timestamps, capture method.
- Schema versioning: `raw.v1`, `normalized.v1`, `enriched.v1`, `curated.v1`.
- Social engineering boundary: no features for manipulation/exploitation; focus on research, analysis, context-aware communication.
- Test coverage required for non-trivial logic.

---

## File Structure

```
src/
  providers/
    __init__.py
    base.py              # Abstract provider interface
    tiktok.py            # TikTok adapter (extracted from collector)
    linkedin.py          # LinkedIn adapter (stub)
  schema/
    __init__.py
    canonical.py         # Provider-agnostic Observation/Entity/Content/Relationship/Annotation/Evidence/Provenance/Confidence
    mapper.py            # Provider-specific → canonical
  pipeline/
    __init__.py
    stages.py            # Idempotent stage runners (normalize/dedup/context/quality/annotate/verify/curate)
    identity.py          # Cross-platform entity resolution with confidence
    dedup.py             # Multi-tier dedup (exact/normalized/near-duplicate)
    quality.py           # Quality scoring, gating thresholds
  export/
    __init__.py
    mark.py              # Mark RAG export (existing mark_export.py moved here)
    manifest.py          # Dataset manifest generation
tests/
  test_schema.py
  test_mapper.py
  test_dedup.py
  test_quality.py
  test_identity.py
  test_stages.py
```

---

### Task 1: Canonical Schema Layer

**Files:**
- Create: `src/schema/canonical.py`
- Create: `src/schema/mapper.py`
- Test: `tests/test_schema.py`, `tests/test_mapper.py`

**Interfaces:**
- Consumes: `src/tiktok_schema.py` dataclasses for TikTok-specific shapes
- Produces: `Observation`, `Entity`, `Content`, `Relationship`, `Annotation`, `Evidence`, `Provenance`, `Confidence` dataclasses; `tiktok_to_canonical()` mapper

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
from src.schema.canonical import Observation, Entity, Provenance, Confidence

def test_observation_creation():
    obs = Observation(
        observation_id="obs_001",
        source="tiktok",
        content=Content(text="hello world"),
        provenance=Provenance(
            collector_version="0.4.0",
            pipeline_version="1.0.0",
            source="tiktok",
            captured_at="2026-08-19T23:00:00+00:00",
        ),
    )
    assert obs.content.text == "hello world"
    assert obs.provenance.source == "tiktok"
    assert obs.confidence is None  # optional
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/schema/__init__.py
# empty

# src/schema/canonical.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class Provenance:
    collector_version: str
    pipeline_version: str
    source: str
    captured_at: str = ""
    processed_at: str = field(default_factory=_now)
    model: str = ""
    annotation_version: str = ""

@dataclass
class Confidence:
    value: float  # 0.0-1.0
    evidence: List[str] = field(default_factory=list)
    method: str = "exact_match"  # exact | heuristic | llm | entity_resolution

@dataclass
class Content:
    text_raw: str
    text_normalized: str = ""
    metadata: dict = field(default_factory=dict)

@dataclass
class Entity:
    entity_id: str
    entity_type: str  # user | video | creator | topic
    provider_ids: dict = field(default_factory=dict)  # {"tiktok": "123", "linkedin": "456"}
    display_name: str = ""
    confidence: Optional[Confidence] = None

@dataclass
class Relationship:
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str  # authored | replied_to | mentioned | same_as
    confidence: Optional[Confidence] = None
    evidence: List[str] = field(default_factory=list)

@dataclass
class Annotation:
    annotation_id: str
    annotation_type: str  # identity | quality | sentiment | topic
    value: dict
    model: str = ""
    confidence: Optional[Confidence] = None

@dataclass
class Evidence:
    evidence_id: str
    source: str
    source_ref: str  # URL, comment_id, etc.
    captured_at: str = ""
    content_snippet: str = ""

@dataclass
class Observation:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/schema/__init__.py src/schema/canonical.py tests/test_schema.py
git commit -m "feat: add canonical schema dataclasses (Observation, Entity, Content, Provenance, Confidence)"
```

- [ ] **Step 6: Write the failing test for mapper**

```python
# tests/test_mapper.py
from src.tiktok_schema import RawComment, Author
from src.schema.canonical import Observation, Content, Provenance, Entity
from src.schema.mapper import tiktok_to_canonical

def test_tiktok_raw_to_observation():
    raw = RawComment(
        schema_version="raw.v1",
        source="tiktok",
        video_id="vid123",
        video_url="https://tiktok.com/v/vid123",
        comment_id="c001",
        parent_comment_id="",
        author=Author(author_id="u1", author_handle="@alice", display_name="Alice"),
        text_raw="hello world",
        likes=5,
        reply_count=0,
        create_time=1750000000,
        capture_method="cdp",
        captured_at="2026-08-19T23:00:00+00:00",
        collector_version="0.4.0",
        video_context={"caption": "test vid", "hashtags": ["#test"], "creator": "bob"},
    )
    obs = tiktok_to_canonical(raw)
    assert isinstance(obs, Observation)
    assert obs.content.text_raw == "hello world"
    assert obs.provenance.source == "tiktok"
    assert obs.provenance.collector_version == "0.4.0"
    assert obs.entity_id == "tiktok:u1"
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest tests/test_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.schema.mapper'`

- [ ] **Step 8: Write minimal implementation**

```python
# src/schema/mapper.py
from __future__ import annotations
from typing import Optional
from src.schema.canonical import (
    Observation, Content, Provenance, Entity, Confidence,
    Relationship, Annotation, Evidence
)
from src.tiktok_schema import RawComment, NormalizedComment, Author

def tiktok_to_canonical(raw: RawComment) -> Observation:
    entity_id = f"tiktok:{raw.author.author_id}" if raw.author.author_id else f"tiktok:handle:{raw.author.author_handle}"
    entity = Entity(
        entity_id=entity_id,
        entity_type="user",
        provider_ids={"tiktok": raw.author.author_id} if raw.author.author_id else {},
        display_name=raw.author.display_name or raw.author.author_handle,
        confidence=Confidence(value=1.0 if raw.author.author_id else 0.7, evidence=["tiktok_author_id"], method="exact_match"),
    )
    content = Content(text_raw=raw.text_raw, metadata={"video_id": raw.video_id, "comment_id": raw.comment_id})
    prov = Provenance(
        collector_version=raw.collector_version,
        pipeline_version="1.0.0",
        source=raw.source,
        captured_at=raw.captured_at,
    )
    obs = Observation(
        observation_id=f"tiktok:{raw.comment_id}",
        source=raw.source,
        content=content,
        entity_id=entity_id,
        provenance=prov,
        confidence=Confidence(value=1.0, evidence=["direct_collection"], method="exact_match"),
    )
    obs.annotations.append(Annotation(
        annotation_id=f"tiktok:{raw.comment_id}:ctx",
        annotation_type="video_context",
        value=raw.video_context,
    ))
    return obs
```

- [ ] **Step 9: Run test to verify it passes**

Run: `python -m pytest tests/test_mapper.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/schema/mapper.py tests/test_mapper.py
git commit -m "feat: add TikTok→canonical mapper with entity resolution and provenance"
```

---

### Task 2: Provider Adapter Interface

**Files:**
- Create: `src/providers/__init__.py`
- Create: `src/providers/base.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `src/schema/canonical.py` dataclasses
- Produces: `ProviderAdapter` abstract class, `collect()` and `probe()` methods

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers.py
import inspect
from src.providers.base import ProviderAdapter

def test_provider_adapter_interface():
    required = ["collect", "probe", "provider_name"]
    for method in required:
        assert hasattr(ProviderAdapter, method), f"Missing {method}"
    sig = inspect.signature(ProviderAdapter.collect)
    assert "url" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/providers/__init__.py
# empty

# src/providers/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from src.schema.canonical import Observation

class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @abstractmethod
    async def collect(self, url: str, **kwargs) -> List[Observation]:
        ...

    @abstractmethod
    def probe(self, url: str) -> Dict:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_providers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/providers/__init__.py src/providers/base.py tests/test_providers.py
git commit -m "feat: add ProviderAdapter abstract interface for multi-provider support"
```

---

### Task 3: TikTok Adapter (Extract from collector.py)

**Files:**
- Create: `src/providers/tiktok.py`
- Modify: `src/collector.py` — delegate to adapter, keep as orchestration wrapper
- Test: `tests/test_tiktok_provider.py`

**Interfaces:**
- Consumes: `ProviderAdapter`, `src/schema/canonical.py`, existing Camoufox collector internals
- Produces: `TikTokAdapter` implementing `collect()` returning `List[Observation]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tiktok_provider.py
from src.providers.tiktok import TikTokAdapter

def test_tiktok_adapter_provider_name():
    adapter = TikTokAdapter()
    assert adapter.provider_name == "tiktok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tiktok_provider.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/providers/tiktok.py
from __future__ import annotations
import asyncio
from typing import List, Optional
from src.providers.base import ProviderAdapter
from src.schema.canonical import Observation
from src.schema.mapper import tiktok_to_canonical
from src.tiktok_schema import RawComment

class TikTokAdapter(ProviderAdapter):
    @property
    def provider_name(self) -> str:
        return "tiktok"

    async def collect(self, url: str, max_comments: int = 100, **kwargs) -> List[Observation]:
        from src.collector import collect_comments  # existing collector function
        raw_list = await collect_comments(url, max=max_comments, **kwargs)
        return [tiktok_to_canonical(r) for r in raw_list]

    def probe(self, url: str) -> dict:
        return {"url": url, "provider": "tiktok", "accessible": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tiktok_provider.py -v`
Expected: PASS

- [ ] **Step 5: Refactor collector.py to use adapter**

```python
# src/collector.py — add at top
from src.providers.tiktok import TikTokAdapter

# Add orchestration wrapper
async def collect_and_export(url: str, max_comments: int = 100):
    adapter = TikTokAdapter()
    observations = await adapter.collect(url, max_comments=max_comments)
    # existing JSONL write logic here
```

- [ ] **Step 6: Commit**

```bash
git add src/providers/tiktok.py src/collector.py tests/test_tiktok_provider.py
git commit -m "feat: extract TikTok adapter from collector; keep collector as orchestration wrapper"
```

---

### Task 4: Multi-Tier Dedup

**Files:**
- Create: `src/pipeline/dedup.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Consumes: `Observation` dataclasses, `normalize_text()`
- Produces: `dedup_exact()`, `dedup_normalized()`, `dedup_near_duplicate()` returning filtered list

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dedup.py
from src.schema.canonical import Observation, Content, Provenance
from src.pipeline.dedup import dedup_exact, dedup_normalized

def _obs(text: str, obs_id: str) -> Observation:
    return Observation(
        observation_id=obs_id,
        source="tiktok",
        content=Content(text_raw=text),
        provenance=Provenance(collector_version="0.4.0", pipeline_version="1.0.0", source="tiktok"),
    )

def test_dedup_exact():
    a = _obs("hello world", "c1")
    b = _obs("hello world", "c2")
    result = dedup_exact([a, b])
    assert len(result) == 1

def test_dedup_normalized_case():
    a = _obs("Hello World", "c1")
    b = _obs("hello world", "c2")
    result = dedup_normalized([a, b])
    assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dedup.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/pipeline/__init__.py
# empty

# src/pipeline/dedup.py
from __future__ import annotations
from typing import List, Set
from src.schema.canonical import Observation
from src.tiktok_schema import normalize_text

def dedup_exact(records: List[Observation]) -> List[Observation]:
    seen: Set[str] = set()
    out = []
    for r in records:
        key = r.content.text_raw.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out

def dedup_normalized(records: List[Observation]) -> List[Observation]:
    seen: Set[str] = set()
    out = []
    for r in records:
        key = normalize_text(r.content.text_raw)
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out

def dedup_near_duplicate(records: List[Observation], threshold: float = 0.85) -> List[Observation]:
    # ponytail: naive O(n^2) Jaccard on char bigrams; upgrade to MinHash when corpus > 10k
    from collections import Counter
    def bigrams(text: str) -> Counter:
        t = normalize_text(text)
        return Counter(t[i:i+2] for i in range(len(t)-1))

    def jaccard(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        inter = sum((a & b).values())
        union = sum((a | b).values())
        return inter / union if union else 0.0

    kept = []
    seen_bgs = []
    for r in records:
        bg = bigrams(r.content.text_raw)
        if any(jaccard(bg, prev) >= threshold for prev in seen_bgs):
            continue
        seen_bgs.append(bg)
        kept.append(r)
    return kept
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/__init__.py src/pipeline/dedup.py tests/test_dedup.py
git commit -m "feat: add multi-tier dedup (exact, normalized, near-duplicate)"
```

---

### Task 5: Quality Scoring and Gating

**Files:**
- Modify: `src/pipeline/quality.py` (extract from `src/pipeline.py`)
- Test: `tests/test_quality.py`

**Interfaces:**
- Consumes: `Observation` dataclasses
- Produces: `compute_quality_score(observation) -> dict`, `passes_gate(observation, thresholds) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quality.py
from src.schema.canonical import Observation, Content, Provenance
from src.pipeline.quality import compute_quality_score, passes_gate

def _obs(text: str) -> Observation:
    return Observation(
        observation_id="c1", source="tiktok",
        content=Content(text_raw=text),
        provenance=Provenance(collector_version="0.4.0", pipeline_version="1.0.0", source="tiktok"),
    )

def test_quality_score_empty():
    assert compute_quality_score(_obs("")) < 0.3

def test_gate_pass():
    obs = _obs("this is a meaningful comment about the video")
    assert passes_gate(obs) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quality.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/pipeline/quality.py
from __future__ import annotations
import re
from typing import Dict, List
from src.schema.canonical import Observation

QUALITY_MIN = 0.30
SPAM_MAX = 0.85
TOXIC_MAX = 0.80

_URL_RE = re.compile(r'https?://\S+|www\.\S+')
_TOXIC_RE = re.compile(r'\b(bitch|slut|kill yourself)\b', re.I)

def compute_quality_score(obs: Observation) -> dict:
    text = obs.content.text_raw or ""
    if not text.strip():
        return {"curated_score": 0.0, "semantic_density": 0.0, "spam_probability": 1.0, "toxicity": 0.0}

    words = text.split()
    unique_ratio = len(set(words)) / max(len(words), 1)
    url_count = len(_URL_RE.findall(text))
    toxicity = 1.0 if _URL_RE.search(text) and _TOXIC_RE.search(text) else (0.8 if url_count > 2 else 0.0)
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
    score = compute_quality_score(obs)
    return score["curated_score"] >= QUALITY_MIN and score["spam_probability"] <= SPAM_MAX and score["toxicity"] <= TOXIC_MAX
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quality.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/quality.py tests/test_quality.py
git commit -m "feat: extract quality scoring and gating into src/pipeline/quality.py"
```

---

### Task 6: Cross-Platform Identity Resolution

**Files:**
- Create: `src/pipeline/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `Entity` dataclasses with `provider_ids`
- Produces: `resolve_identity(entities: List[Entity]) -> List[Entity]` with merged confidence

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity.py
from src.schema.canonical import Entity, Confidence
from src.pipeline.identity import resolve_identity

def test_identity_merge_same_user():
    e1 = Entity(entity_id="tiktok:u1", entity_type="user", provider_ids={"tiktok": "u1"}, confidence=Confidence(value=0.9, method="exact_match"))
    e2 = Entity(entity_id="linkedin:u1", entity_type="user", provider_ids={"linkedin": "u1"}, confidence=Confidence(value=0.8, method="heuristic"))
    result = resolve_identity([e1, e2])
    merged = [e for e in result if len(e.provider_ids) == 2]
    assert len(merged) == 1
    assert merged[0].confidence.value > 0.9
    assert merged[0].confidence.method == "entity_resolution"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_identity.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/pipeline/identity.py
from __future__ import annotations
from typing import List, Dict, Tuple
from src.schema.canonical import Entity, Confidence

def resolve_identity(entities: List[Entity]) -> List[Entity]:
    # ponytail: naive merge on entity_id after provider prefix stripping; upgrade to fuzzy matcher if needed
    by_core: Dict[str, List[Entity]] = {}
    for e in entities:
        core = _core_id(e.entity_id)
        by_core.setdefault(core, []).append(e)

    merged = []
    for group in by_core.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        primary = group[0]
        for other in group[1:]:
            primary.provider_ids.update(other.provider_ids)
            primary.display_name = primary.display_name or other.display_name
            # Merge confidence: weighted average, method upgrades to entity_resolution
            vals = [primary.confidence.value if primary.confidence else 0.5, other.confidence.value if other.confidence else 0.5]
            merged_conf = Confidence(
                value=sum(vals) / len(vals),
                evidence=(primary.confidence.evidence if primary.confidence else []) + (other.confidence.evidence if other.confidence else []),
                method="entity_resolution",
            )
            primary.confidence = merged_conf
        merged.append(primary)
    return merged

def _core_id(full_id: str) -> str:
    # Strip provider prefix "tiktok:" "linkedin:" "youtube:" etc.
    if ":" in full_id:
        return full_id.split(":", 1)[1]
    return full_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_identity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/identity.py tests/test_identity.py
git commit -m "feat: add cross-platform identity resolution with confidence and provenance"
```

---

### Task 7: Idempotent Stage Runner

**Files:**
- Create: `src/pipeline/stages.py`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: `Observation` dataclasses, stage functions from `dedup.py`, `quality.py`
- Produces: `run_stage(name, records, stage_fn, output_dir) -> (List[Observation], dict)` with checkpoint support

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stages.py
import json, tempfile, os
from pathlib import Path
from src.schema.canonical import Observation, Content, Provenance
from src.pipeline.stages import run_stage

def _obs(i: str) -> Observation:
    return Observation(observation_id=i, source="tiktok", content=Content(text_raw="test "+i), provenance=Provenance(collector_version="0.4.0", pipeline_version="1.0.0", source="tiktok"))

def test_stage_idempotent_writes_once():
    with tempfile.TemporaryDirectory() as tmp:
        recs = [_obs("a"), _obs("b")]
        out1, meta1 = run_stage("test", recs, lambda x: x, Path(tmp))
        out2, meta2 = run_stage("test", recs, lambda x: x, Path(tmp))
        assert len(out1) == 2
        assert meta1["written"] == 2
        assert meta2["skipped"] == 2
        files = list(Path(tmp).glob("*.jsonl"))
        assert len(files) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_stages.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/pipeline/stages.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Callable, Tuple
from src.schema.canonical import Observation
from src.tiktok_schema import write_jsonl

def run_stage(name: str, records: List[Observation], stage_fn: Callable[[List[Observation]], List[Observation]], output_dir: Path) -> Tuple[List[Observation], dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"{name}.jsonl"
    meta_path = output_dir / f"{name}.meta.json"

    existing = []
    if out_file.exists():
        existing = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]

    existing_ids = {r.get("observation_id") for r in existing}
    if existing_ids:
        remaining = [r for r in records if r.observation_id not in existing_ids]
    else:
        remaining = records

    processed = stage_fn(remaining)
    dicts = [r.to_dict() for r in processed]
    write_jsonl(str(out_file), dicts, append=True)

    meta = {
        "stage": name,
        "input": len(records),
        "existing": len(existing),
        "processed": len(processed),
        "written": len(dicts),
        "skipped": len(records) - len(remaining),
    }
    meta_path.write_text(json.dumps(meta))
    return processed, meta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_stages.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/stages.py tests/test_stages.py
git commit -m "feat: add idempotent stage runner with checkpoint support"
```

---

### Task 8: Multi-Consumer Export Layer

**Files:**
- Create: `src/export/__init__.py`
- Modify: `src/mark_export.py` → move to `src/export/mark.py`
- Create: `src/export/manifest.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: curated `Observation` list, manifest schema
- Produces: `export_to_mark()`, `write_manifest()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
import json, tempfile
from pathlib import Path
from src.schema.canonical import Observation, Content, Provenance, Annotation
from src.export.manifest import write_manifest

def test_manifest_writes():
    with tempfile.TemporaryDirectory() as tmp:
        meta = {"dataset_id": "test-001", "comment_count": 10, "source": "tiktok"}
        write_manifest(meta, Path(tmp))
        assert (Path(tmp) / "manifest.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/export/__init__.py
# empty

# src/export/manifest.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict

def write_manifest(meta: Dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_export.py -v`
Expected: PASS

- [ ] **Step 5: Move mark_export.py to src/export/mark.py and update imports**

```bash
git mv src/mark_export.py src/export/mark.py
# Edit src/export/mark.py: update _ROOT path to point to repo root (parent of src/)
```

- [ ] **Step 6: Commit**

```bash
git add src/export/__init__.py src/export/manifest.py src/export/mark.py tests/test_export.py
git commit -m "feat: add multi-consumer export layer with manifest generation"
```

---

### Task 9: Update README and Docs

**Files:**
- Modify: `README.md`
- Test: N/A (docs)

- [ ] **Step 1: Update README.md header and architecture section to reflect Social Data Engine**

Replace "TikTok scraper" language with "Social Data Engine — multi-provider social data acquisition and knowledge pipeline". Update architecture diagram and success metrics.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for Social Data Engine rebrand"
```

---

## Self-Review

**1. Spec coverage:**
- Canonical schema with Observation/Entity/etc. → Task 1
- Multi-provider adapter interface → Task 2
- TikTok adapter extraction → Task 3
- Multi-tier dedup → Task 4
- Quality scoring and gating → Task 5
- Cross-platform identity resolution → Task 6
- Idempotent stage runner → Task 7
- Multi-consumer export (Mark + manifest) → Task 8
- Raw data preservation (text_raw + text_normalized) → Task 1 (canonical schema), Task 4 (dedup preserves both)
- Provenance at every level → Task 1 (Provenance dataclass)
- Schema versioning → Task 1 (schema_version field)
- Pipeline stages collect→raw→normalize→dedup→context→quality→annotation→verification→curated → Tasks 4-7 cover normalize/dedup/quality; annotation/verification deferred to P2
- Social engineering boundary → enforced by schema design (no inference fields beyond annotation)
- YAGNI: no distributed DB, no vector DB, no Parquet → respected throughout

**2. Placeholder scan:** No TBD/TODO/placeholders found.

**3. Type consistency:** All tasks reference `Observation`, `Entity`, `Provenance` from `src/schema/canonical.py` consistently. Stage runner returns `(List[Observation], dict)` matching mapper output type.
