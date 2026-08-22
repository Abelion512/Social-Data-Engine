#!/usr/bin/env python3
"""
Deterministic tests for the minimal Harness / Actor Contract
(src/runtime/harness.py + extended AcquisitionActor).

Proves (stdlib only, no browser, no network):
 1. An actor declares capabilities → recorded into run provenance (vocabulary
    ONLY — nothing evaluates them)
 2. RunInput serializes round-trip; malformed inputs fail closed
 3. A fake actor executes through ActorHarness → AcquisitionRuntime unchanged
 4. A TikTok-shaped actor uses the SAME runtime instance unmodified, with
    checkpoint/resume behavior intact
 5. Adversarial cases:
      - zero capabilities allowed            (documented behavior)
      - duplicate capabilities rejected
      - non-Capability / None declarations rejected
      - missing/invalid actor identity rejected by the HARNESS while the raw
        runtime stays backward-compatible (empty provenance, still runs)
      - two actor versions produce distinguishable run metadata
      - unknown-but-well-formed capability names are representable (open
        vocabulary; membership is future-evaluator territory)
      - malformed RunInput.from_dict fails closed (ValueError, not KeyError)
      - no provider-specific field leaks into the generic contract models

Run:  python -m pytest tests/test_actor_harness.py -v
      python tests/test_actor_harness.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.policy.models import POLICY_MODEL_VERSION, Capability
from src.runtime import (
    AcquisitionActor,
    AcquisitionRuntime,
    ActorHarness,
    Outcome,
    PageResult,
    RunInput,
    RunLifecycle,
    RunOptions,
    lifecycle_for_outcome,
)
from src.providers.tiktok_actor import (
    CollectorApiPageSource,
    TikTokAcquisitionActor,
    parse_tiktok_video_id,
    tiktok_api_page_to_page_result,
)
from src.tiktok_schema import COLLECTOR_VERSION


def run(coro):
    return asyncio.run(coro)


def read_lines(path) -> List[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def make_harness(tmpdir: str) -> ActorHarness:
    return ActorHarness(
        state_dir=str(Path(tmpdir) / "state"),
        data_dir=str(Path(tmpdir) / "data"),
        print_fn=lambda *_: None,
    )


def make_input(actor, url="https://example.test/target/1", **kw) -> RunInput:
    return RunInput(
        actor_id=actor.actor_id,
        actor_version=actor.actor_version,
        provider=kw.pop("provider", "fakeprovider"),
        target_url=url,
        **kw,
    )


class FakeActor(AcquisitionActor):
    """Fully-contract-conformant fake actor (a 'second provider' shape)."""

    provider_name = "fakeprovider"
    id_key = "item_id"
    actor_id = "acquisition.fake"
    actor_version = "1.0.0"

    def __init__(self, pages=None, caps=None):
        self.pages = pages if pages is not None else [
            [{"item_id": f"f_{i}", "text": f"item {i}"} for i in range(10)],
        ]
        self.caps = caps if caps is not None else ()
        self.calls: List[tuple] = []

    def capabilities(self):
        return self.caps

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        self.calls.append((cursor, page_index))
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            pg = self.pages[idx]
            nxt = cursor if isinstance(cursor, int) else 0
            return PageResult(items=pg, next_cursor=nxt + len(pg),
                              has_more=idx < len(self.pages) - 1)
        return PageResult(items=[], next_cursor=cursor, has_more=False)


# ── 1+3. Declaration recorded; fake actor runs through harness ────────────────

def test_fake_actor_declares_capabilities_and_executes_through_runtime():
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        caps = (Capability(name="network.fetch"), Capability(name="filesystem.read"))
        actor = FakeActor(caps=caps)
        inp = make_input(actor)

        s = run(harness.run(actor, inp))

        # Runtime semantics unchanged
        assert s.outcome == Outcome.SUCCESS
        assert s.lifecycle_state == RunLifecycle.COMPLETED
        assert s.items_seen == 10
        assert s.dataset_path and s.checkpoint_path
        # Actor provenance flows into the summary
        assert s.actor_id == "acquisition.fake"
        assert s.actor_version == "1.0.0"

        # Provenance lands on disk: checkpoint target carries declaration
        ckpt = json.loads(Path(s.checkpoint_path).read_text())
        target = ckpt["target"]
        assert target["actor_id"] == "acquisition.fake"
        assert target["actor_version"] == "1.0.0"
        assert target["capabilities"] == ["network.fetch", "filesystem.read"]
        assert target["policy_model_version"] == POLICY_MODEL_VERSION
        # Additive lifecycle provenance in the final checkpoint
        assert ckpt["lifecycle"] == RunLifecycle.COMPLETED
        # Legacy status values untouched
        assert ckpt["status"] == "done"


# ── 2. RunInput serialization ────────────────────────────────────────────────

def test_run_input_serializes_round_trip():
    inp = RunInput(
        actor_id="acquisition.x", actor_version="2.3.1", provider="prov",
        target_url="https://x.test/1",
        payload={"video_id": "42"}, config={"max_items": 7},
        job_id="job1", run_id="run1",
    )
    d = inp.to_dict()
    assert RunInput.from_dict(d) == inp
    # minimal form also round-trips
    mini = RunInput(actor_id="a.b", actor_version="1", provider="p",
                    target_url="https://x.test/")
    assert RunInput.from_dict(mini.to_dict()) == mini


def test_malformed_runinput_fails_closed_valueerror_not_keyerror():
    good = {
        "actor_id": "a.b", "actor_version": "1.0.0", "provider": "p",
        "target_url": "https://x.test/",
    }
    # missing required keys → ValueError (loud, never KeyError)
    for key in ("actor_id", "actor_version", "provider", "target_url"):
        broken = {k: v for k, v in good.items() if k != key}
        try:
            RunInput.from_dict(broken)
            raise AssertionError(f"missing {key} must fail closed")
        except ValueError:
            pass
    # unknown keys rejected (typo-proof configuration)
    try:
        RunInput.from_dict({**good, "actore_id": "typo"})
        raise AssertionError("unknown keys must be rejected")
    except ValueError:
        pass
    # non-dict input rejected
    try:
        RunInput.from_dict("nope")  # type: ignore[arg-type]
        raise AssertionError("non-dict must be rejected")
    except ValueError:
        pass
    # constructor-level validation
    cases = [
        lambda: RunInput(actor_id="", actor_version="1", provider="p", target_url="u"),
        lambda: RunInput(actor_id="BAD SPACE", actor_version="1", provider="p", target_url="u"),
        lambda: RunInput(actor_id="a.b", actor_version="", provider="p", target_url="u"),
        lambda: RunInput(actor_id="a.b", actor_version="1 0", provider="p", target_url="u"),
        lambda: RunInput(actor_id="a.b", actor_version="1", provider="", target_url="u"),
        lambda: RunInput(actor_id="a.b", actor_version="1", provider="p", target_url="  "),
        lambda: RunInput(actor_id="a.b", actor_version="1", provider="p", target_url="u", payload=["x"]),  # type: ignore[arg-type]
        lambda: RunInput(actor_id="a.b", actor_version="1", provider="p", target_url="u", config={1: "x"}),  # type: ignore[dict-item]
    ]
    for case in cases:
        try:
            case()
            raise AssertionError("invalid RunInput must raise ValueError")
        except ValueError:
            pass


# ── Adversarial: capability declarations ─────────────────────────────────────

def test_zero_capabilities_is_valid_and_recorded():
    """Q1: an actor may declare ZERO capabilities and still run. Declaration
    is vocabulary/configuration only — nothing requires any."""
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        actor = FakeActor(caps=())
        s = run(harness.run(actor, make_input(actor)))
        ckpt = json.loads(Path(s.checkpoint_path).read_text())
        assert ckpt["target"]["capabilities"] == []


def test_duplicate_capability_declarations_rejected():
    """Q2: duplicates are an author bug — rejected before execution."""
    dup = Capability(name="network.fetch")
    actor = FakeActor(caps=(dup, Capability(name="network.fetch")))
    try:
        ActorHarness.validate_declared_capabilities(actor)
        raise AssertionError("duplicate capability must be rejected")
    except ValueError:
        pass


def test_non_capability_and_none_declarations_rejected():
    try:
        ActorHarness.validate_declared_capabilities(
            FakeActor(caps=("network.fetch",)))  # raw string, not Capability
        raise AssertionError("raw string capability must be rejected")
    except ValueError:
        pass
    none_actor = FakeActor()
    none_actor.caps = None
    try:
        ActorHarness.validate_declared_capabilities(none_actor)
        raise AssertionError("None declaration must be rejected")
    except ValueError:
        pass


def test_unknown_but_wellformed_capability_names_representable():
    """Q5: the vocabulary is intentionally OPEN — a well-formed name outside
    the seed constants is structurally valid and recorded verbatim. Membership
    enforcement belongs to the future policy evaluator, NOT to declaration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        custom = Capability(name="platform.custom-verb")
        actor = FakeActor(caps=(custom,))
        s = run(harness.run(actor, make_input(actor)))
        ckpt = json.loads(Path(s.checkpoint_path).read_text())
        assert ckpt["target"]["capabilities"] == ["platform.custom-verb"]


# ── Adversarial: identity ────────────────────────────────────────────────────

def test_legacy_actor_without_identity_still_runs_on_raw_runtime():
    """Q3: the raw runtime stays backward-compatible with pre-contract actors
    (empty identity provenance); the HARNESS rejects them fail-closed."""
    with tempfile.TemporaryDirectory() as tmpdir:

        class LegacyActor(AcquisitionActor):
            provider_name = "legacy"
            id_key = "item_id"

            async def fetch_page(self, ctx, cursor, page_index):
                return PageResult(items=[{"item_id": "l1"}],
                                  next_cursor=1, has_more=False)

        legacy = LegacyActor()
        assert legacy.actor_id == "" and legacy.actor_version == ""
        rt = AcquisitionRuntime(print_fn=lambda *_: None)
        from src.runtime import RunContext
        ctx = RunContext.create(provider="legacy", target_url="https://x.test/1",
                                job_id="j_legacy",
                                state_dir=str(Path(tmpdir) / "s"),
                                data_dir=str(Path(tmpdir) / "d"))
        s = run(rt.run(legacy, ctx))
        assert s.items_seen == 1
        assert s.actor_id == "" and s.actor_version == ""  # honest emptiness

    harness = make_harness("/nonexistent-for-validation")
    # Valid RunInput, but the actor object lacks contract identity → the
    # HARNESS rejects it fail-closed (identity validation runs before binding).
    inp_for_fake = RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                            provider="fakeprovider", target_url="https://x.test/")
    try:
        run(harness.run(LegacyActor(), inp_for_fake))
        raise AssertionError("harness must reject actors without identity")
    except ValueError:
        pass


def test_identity_mismatch_between_runinput_and_actor_rejected():
    harness = make_harness("/nonexistent-for-validation")
    actor = FakeActor()
    wrong_id = RunInput(actor_id="acquisition.other", actor_version="1.0.0",
                        provider="fakeprovider", target_url="https://x.test/")
    wrong_ver = RunInput(actor_id="acquisition.fake", actor_version="9.9.9",
                         provider="fakeprovider", target_url="https://x.test/")
    for bad in (wrong_id, wrong_ver):
        try:
            run(harness.run(actor, bad))
            raise AssertionError("identity mismatch must fail closed")
        except ValueError:
            pass


def test_two_actor_versions_produce_distinguishable_metadata():
    """Q4: bumping actor_version visibly changes run provenance end-to-end."""
    versions = []
    for version in ("1.0.0", "1.1.0"):
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = make_harness(tmpdir)
            actor = FakeActor()
            actor.actor_version = version
            s = run(harness.run(actor, make_input(actor)))
            assert s.actor_version == version
            ckpt = json.loads(Path(s.checkpoint_path).read_text())
            assert ckpt["target"]["actor_version"] == version
            versions.append((s.actor_version, ckpt["target"]["actor_version"]))
    assert versions[0] != versions[1]


# ── Configuration projection ─────────────────────────────────────────────────

def test_config_projects_onto_run_options_and_validates():
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        big_page = [{"item_id": f"c_{i}"} for i in range(30)]
        actor = FakeActor(pages=[big_page])
        inp = make_input(actor, config={"max_items": 5})
        s = run(harness.run(actor, inp))
        assert s.items_seen == 5                       # config axis honored
        assert s.termination_reason == "max_comments_reached"

    harness = make_harness("/nonexistent-for-validation")
    actor = FakeActor()
    try:
        run(harness.run(actor, make_input(actor, config={"bogus_axis": 1})))
        raise AssertionError("unknown config axis must fail loudly")
    except ValueError:
        pass
    try:
        run(harness.run(actor, make_input(actor, config={"max_items": True})))
        raise AssertionError("bool for int axis must fail loudly")
    except ValueError:
        pass
    try:
        run(harness.run(actor, make_input(actor, config={"max_items": 3}),
                        options=RunOptions()))
        raise AssertionError("config + explicit options is ambiguous")
    except ValueError:
        pass


# ── Provider-leakage guard ───────────────────────────────────────────────────

def test_generic_contract_carries_no_provider_specific_fields():
    """Q7: the generic contract models expose NO platform-specific field;
    provider quirks live only in free-form dicts / provider modules."""
    from src.runtime.context import RunContext
    from src.runtime.engine import RunSummary
    forbidden = ("tiktok", "cid", "comment_id", "aweme", "linkedin")
    for model in (RunInput, RunSummary, RunContext):
        names = [f.name for f in dataclasses.fields(model)]
        leaked = [n for n in names for f in forbidden if f in n.lower()]
        assert not leaked, f"{model.__name__} leaks provider specifics: {leaked}"
    # ...and a differently-shaped second actor needs zero runtime changes:
    with tempfile.TemporaryDirectory() as tmpdir:
        rt = AcquisitionRuntime(print_fn=lambda *_: None)
        harness_a = ActorHarness(runtime=rt, print_fn=lambda *_: None,
                                 state_dir=str(Path(tmpdir) / "sa"),
                                 data_dir=str(Path(tmpdir) / "da"))
        s_a = run(harness_a.run(FakeActor(), make_input(FakeActor())))

        class OtherShapeActor(AcquisitionActor):
            provider_name = "other"
            id_key = "post_pk"
            actor_id = "acquisition.other"
            actor_version = "0.1.0"

            async def fetch_page(self, ctx, cursor, page_index):
                if page_index == 0:
                    return PageResult(items=[{"post_pk": "p1"}],
                                      next_cursor=1, has_more=False)
                return PageResult(items=[])

        harness_b = ActorHarness(runtime=rt, print_fn=lambda *_: None,
                                 state_dir=str(Path(tmpdir) / "sb"),
                                 data_dir=str(Path(tmpdir) / "db"))
        other = OtherShapeActor()
        s_b = run(harness_b.run(other, make_input(other, provider="other")))
        assert s_a.provider == "fakeprovider" and s_b.provider == "other"
        assert s_b.items_seen == 1
        assert s_b.lifecycle_state == RunLifecycle.COMPLETED


# ── Lifecycle taxonomy ───────────────────────────────────────────────────────

def test_lifecycle_mapping_total_over_outcomes():
    assert lifecycle_for_outcome(Outcome.SUCCESS) == RunLifecycle.COMPLETED
    assert lifecycle_for_outcome(Outcome.CAP_REACHED) == RunLifecycle.COMPLETED
    assert lifecycle_for_outcome(Outcome.AUTH_BLOCKED) == RunLifecycle.TERMINATED
    assert lifecycle_for_outcome(Outcome.PAGINATION_STALL) == RunLifecycle.TERMINATED
    assert lifecycle_for_outcome(Outcome.RETRYABLE_FAILURE) == RunLifecycle.FAILED
    assert lifecycle_for_outcome(Outcome.PERMANENT_FAILURE) == RunLifecycle.FAILED
    # fail closed: unknown outcomes are failures, never successes
    assert lifecycle_for_outcome("") == RunLifecycle.FAILED
    assert lifecycle_for_outcome("mystery") == RunLifecycle.FAILED
    assert set(RunLifecycle.ALL) == {"created", "started", "completed",
                                     "failed", "terminated"}


def test_auth_blocked_run_lifecycle_is_terminated():
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        actor = FakeActor(pages=[])

        async def auth_blocked(ctx, cursor, page_index):
            return PageResult(error="captcha challenge", error_type="auth")

        actor.fetch_page = auth_blocked  # type: ignore[method-assign]
        s = run(harness.run(actor, make_input(actor)))
        assert s.termination_reason == "auth_blocked"
        assert s.lifecycle_state == RunLifecycle.TERMINATED


# ── 4. TikTok-shaped actor on the SAME runtime ───────────────────────────────

def _historical_script_pages():
    """The proven live-pagination script around the ~198 boundary, in RAW
    TikTok API shape ({comments, cursor, has_more}) — served per call so the
    transient empty page retries the same cursor."""
    pages = [
        {"comments": [{"comment_id": f"c_{i}", "text": f"x{i}"} for i in range(100)],
         "cursor": 100, "has_more": 1},
        {"comments": [{"comment_id": f"c_{i}", "text": f"x{i}"} for i in range(100, 198)],
         "cursor": 198, "has_more": 1},
        {"comments": [], "cursor": 198, "has_more": 1},   # transient empty page
        {"comments": [{"comment_id": f"c_{i}", "text": f"x{i}"} for i in range(198, 248)],
         "cursor": 248, "has_more": 0},
    ]
    calls = {"n": 0}

    def page_source(cursor, page_index):
        n = calls["n"]
        calls["n"] += 1
        return pages[n] if n < len(pages) else {"comments": [], "cursor": cursor, "has_more": 0}

    return page_source


def test_raw_tiktok_page_conversion_classifies_malformed_pages():
    ok = tiktok_api_page_to_page_result(
        {"comments": [{"comment_id": "c1"}], "cursor": 5, "has_more": 0})
    assert [i["comment_id"] for i in ok.items] == ["c1"]
    assert ok.next_cursor == 5 and ok.has_more is False   # 0 → False

    bad_shape = tiktok_api_page_to_page_result(["not", "a", "dict"])
    assert bad_shape.error and bad_shape.error_type == "parse_failure"
    bad_items = tiktok_api_page_to_page_result({"comments": ["x"], "cursor": 1})
    assert bad_items.error and bad_items.error_type == "parse_failure"


def test_tiktok_actor_through_shared_runtime_resume_intact():
    """The migrated TikTok path: identity + declaration + shaped payloads run
    through the SAME runtime as the fake actor; interruption/resume keeps
    checkpoint behavior byte-compatible (interrupt at cap, resume, no dupes).
    Scraping semantics unchanged — the live browser loop is NOT touched here;
    this proves contract + runtime equivalence deterministically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rt = AcquisitionRuntime(print_fn=lambda *_: None)   # ONE shared runtime
        harness = ActorHarness(runtime=rt, print_fn=lambda *_: None,
                               state_dir=str(Path(tmpdir) / "s"),
                               data_dir=str(Path(tmpdir) / "d"))

        actor = TikTokAcquisitionActor(page_source=_historical_script_pages())
        inp = make_input(
            actor,
            url="https://www.tiktok.com/@user/video/123",
            provider="tiktok",
            payload={"video_id": "123"},
            # Resume requires a STABLE job identity: without an explicit
            # job_id each invocation derives a timestamped one and would look
            # at a different checkpoint path.
            job_id="job_tt_harness",
        )
        # Sanity: declaration is the honest TikTok access mode, vocabulary only
        assert [(c.name, c.description != "") for c in actor.capabilities()] == [
            ("network.fetch", True), ("browser.automate", True)]
        assert actor.actor_version == COLLECTOR_VERSION

        # Invocation 1: page-cap interrupts after the first full batch
        s1 = run(harness.run(actor, inp, options=RunOptions(max_pages=1)))
        assert s1.provider == "tiktok"
        assert s1.actor_id == "acquisition.tiktok"
        assert s1.termination_reason == "max_pages_reached"
        assert s1.outcome == Outcome.CAP_REACHED
        assert s1.lifecycle_state == RunLifecycle.COMPLETED   # cap = planned stop
        ckpt1 = json.loads(Path(s1.checkpoint_path).read_text())
        assert ckpt1["target"]["capabilities"] == ["network.fetch", "browser.automate"]
        assert ckpt1["target"]["policy_model_version"] == POLICY_MODEL_VERSION
        assert ckpt1["target"]["video_id"] == "123"          # payload rides along

        # Invocation 2: resume continues from the checkpoint, zero duplicates.
        # (Fresh non-resume rerun over a populated dataset path is the documented
        # known limitation — never exercised here.)
        s2 = run(harness.run(actor, inp, options=RunOptions(resume=True)))
        assert s2.resumed is True
        assert s2.termination_reason == "has_more_false"
        assert s2.outcome == Outcome.SUCCESS
        lines = read_lines(s2.dataset_path)
        ids = [r["comment_id"] for r in lines]
        assert len(ids) == len(set(ids)) == 248               # past 198 boundary
        assert s2.items_seen == 248
        assert s2.lifecycle_state == RunLifecycle.COMPLETED


def test_tiktok_actor_malformed_source_terminates_as_parse_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        actor = TikTokAcquisitionActor(
            page_source=lambda cursor, pi: {"comments": "garbage", "cursor": 0})
        inp = make_input(actor, provider="tiktok", config={"max_parse_retries": 1})
        s = run(harness.run(actor, inp))
        assert s.termination_reason == "parse_failure"
        assert s.outcome == Outcome.RETRYABLE_FAILURE
        assert s.lifecycle_state == RunLifecycle.FAILED
        ckpt = json.loads(Path(s.checkpoint_path).read_text())
        assert ckpt["lifecycle"] == RunLifecycle.FAILED


def test_tiktok_actor_requires_page_source_fail_fast():
    try:
        TikTokAcquisitionActor()
        raise AssertionError("page_source-less actor must fail fast")
    except ValueError:
        pass


# ── Hardening: REAL collector page source wired via dependency injection ──────

class _FakeApiPage:
    """Offline stand-in for a live browser page evaluating the comments-API JS.

    Serves recorded body payloads in order (the final body repeats with
    has_more=0 so pagination terminates) and records every evaluated JS call,
    so tests can assert the UNMODIFIED fetch_comments_api wire parameters
    (aweme_id / count / cursor).
    """

    def __init__(self, bodies):
        self.bodies = [dict(b) for b in bodies]
        self.calls = []

    async def evaluate(self, js):
        import re as _re
        m = _re.search(r"aweme_id=(\d+)&count=(\d+)&cursor=(\d+)", js)
        self.calls.append({
            "aweme_id": m.group(1) if m else None,
            "count": int(m.group(2)) if m else None,
            "cursor": int(m.group(3)) if m else None,
        })
        if len(self.bodies) > 1:
            body = self.bodies.pop(0)
        else:
            body = dict(self.bodies[0])
            body["has_more"] = 0
        return json.dumps({"status_code": 200, "body": json.dumps(body)})


def _script_pages(n_items, per_page, prefix="c"):
    """TikTok-shaped top-level pages: {comments, cursor, has_more}."""
    pages = []
    for start in range(0, n_items, per_page):
        chunk = [{"comment_id": f"{prefix}_{i}", "text": f"t{i}"}
                 for i in range(start, min(start + per_page, n_items))]
        pages.append({"comments": chunk,
                      "cursor": min(start + per_page, n_items),
                      "has_more": 1 if start + per_page < n_items else 0})
    return pages


def test_production_source_parses_url_and_validates_arguments():
    assert parse_tiktok_video_id("https://www.tiktok.com/@u/video/123") == "123"
    assert parse_tiktok_video_id("https://www.tiktok.com/@u/photo/456") == "456"
    for bad in ("https://example.com/x/1", "", None):
        try:
            parse_tiktok_video_id(bad)
            raise AssertionError(f"must reject {bad!r}")
        except ValueError:
            pass
    try:
        CollectorApiPageSource(None, "123")
        raise AssertionError("page=None must be rejected")
    except ValueError:
        pass
    try:
        CollectorApiPageSource(object(), "abc")
        raise AssertionError("non-numeric video_id must be rejected")
    except ValueError:
        pass


def test_collector_api_source_delegates_with_unmodified_parameters():
    page = _FakeApiPage([
        {"comments": [{"comment_id": "c_1", "text": "hi"}], "cursor": 50, "has_more": 1},
        {"comments": [{"comment_id": "c_2", "text": "yo"}], "cursor": 100, "has_more": 0},
    ])
    src = CollectorApiPageSource(page, "123", count=50)
    p1 = tiktok_api_page_to_page_result(run(src(0, 0)))
    assert [(c["aweme_id"], c["count"], c["cursor"]) for c in page.calls] == [
        ("123", 50, 0)]
    assert [i["comment_id"] for i in p1.items] == ["c_1"]
    assert p1.has_more is True
    p2 = tiktok_api_page_to_page_result(run(src(50, 1)))
    assert [c["cursor"] for c in page.calls] == [0, 50]
    assert [i["comment_id"] for i in p2.items] == ["c_2"]
    assert p2.has_more is False


def test_async_real_source_through_shared_runtime_resume_intact():
    """The production source SHAPE (async callable, cursor-driven) keeps
    checkpoint/resume semantics intact through the SAME shared runtime."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rt = AcquisitionRuntime(print_fn=lambda *_: None)   # ONE shared runtime
        harness = ActorHarness(runtime=rt, print_fn=lambda *_: None,
                               state_dir=str(Path(tmpdir) / "s"),
                               data_dir=str(Path(tmpdir) / "d"))
        pages = _script_pages(30, 10)

        async def real_shaped_source(cursor, page_index):
            # Cursor-driven selection mirrors fetch_comments_api semantics.
            cur = cursor if isinstance(cursor, int) else 0
            return pages[min(len(pages) - 1, cur // 10)]

        actor = TikTokAcquisitionActor(page_source=real_shaped_source)
        inp = make_input(actor, url="https://www.tiktok.com/@u/video/777",
                         provider="tiktok", payload={"video_id": "777"},
                         job_id="job_tt_async")
        s1 = run(harness.run(actor, inp, options=RunOptions(max_pages=1)))
        assert s1.outcome == Outcome.CAP_REACHED
        ckpt1 = json.loads(Path(s1.checkpoint_path).read_text())
        assert ckpt1["target"]["video_id"] == "777"

        s2 = run(harness.run(actor, inp, options=RunOptions(resume=True)))
        assert s2.outcome == Outcome.SUCCESS
        ids = [r["comment_id"] for r in read_lines(s2.dataset_path)]
        assert len(ids) == len(set(ids)), "duplicates after resume"
        assert sorted(ids) == sorted(f"c_{i}" for i in range(30))


def test_collector_api_source_auth_blocked_classified_not_success():
    class _VerifyPage:
        async def evaluate(self, js):
            return json.dumps({"status_code": 10001,
                               "body": json.dumps({"status_code": 10001,
                                                   "status_msg": "verify your account",
                                                   "comments": []})})

    raw = run(CollectorApiPageSource(_VerifyPage(), "123")(0, 0))
    assert raw.get("error_type") == "auth_blocked"
    pr = tiktok_api_page_to_page_result(raw)
    assert pr.items == []
    assert pr.error_type == "auth_blocked"      # collector verdict survives

    with tempfile.TemporaryDirectory() as tmpdir:
        harness = make_harness(tmpdir)
        actor = TikTokAcquisitionActor(page_source=_VerifyPage())
        inp = make_input(actor, url="https://www.tiktok.com/@u/video/123",
                         provider="tiktok", payload={"video_id": "123"})
        s = run(harness.run(actor, inp))
        assert s.outcome != Outcome.SUCCESS           # classified, never faked success
        assert s.lifecycle_state == RunLifecycle.TERMINATED
        ds = Path(s.dataset_path)
        assert not ds.exists() or read_lines(ds) == []   # zero fabricated records


if __name__ == "__main__":
    tests = [
        test_fake_actor_declares_capabilities_and_executes_through_runtime,
        test_run_input_serializes_round_trip,
        test_malformed_runinput_fails_closed_valueerror_not_keyerror,
        test_zero_capabilities_is_valid_and_recorded,
        test_duplicate_capability_declarations_rejected,
        test_non_capability_and_none_declarations_rejected,
        test_unknown_but_wellformed_capability_names_representable,
        test_legacy_actor_without_identity_still_runs_on_raw_runtime,
        test_identity_mismatch_between_runinput_and_actor_rejected,
        test_two_actor_versions_produce_distinguishable_metadata,
        test_config_projects_onto_run_options_and_validates,
        test_generic_contract_carries_no_provider_specific_fields,
        test_lifecycle_mapping_total_over_outcomes,
        test_auth_blocked_run_lifecycle_is_terminated,
        test_raw_tiktok_page_conversion_classifies_malformed_pages,
        test_tiktok_actor_through_shared_runtime_resume_intact,
        test_tiktok_actor_malformed_source_terminates_as_parse_failure,
        test_tiktok_actor_requires_page_source_fail_fast,
        test_production_source_parses_url_and_validates_arguments,
        test_collector_api_source_delegates_with_unmodified_parameters,
        test_async_real_source_through_shared_runtime_resume_intact,
        test_collector_api_source_auth_blocked_classified_not_success,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__} ✅")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__} ❌ : {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__} ❌ : {type(e).__name__}: {e}")
            failed += 1
    print(f"\nActor Harness Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
