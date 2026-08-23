#!/usr/bin/env python3
"""
Adversarial failure-path tests for the §3.D security gates
(docs/SECURITY-THREAT-MODEL.md §3.D / S-G2 + S-G1 part 1 + S-G4 + S-G8 stub).

Proves (stdlib only, deterministic, no browser/network):

  1. traversal identifiers are REJECTED at RunInput construction
     (error names the field)
  2. absolute / out-of-root locations are REJECTED by rooted-path resolution
     in RunContext.create — including hostile ids reaching the RAW runtime
     path that bypasses RunInput (defense in depth)
  3. legitimate ids keep checkpoint + dataset STRICTLY inside their roots
  4. a default-constructed ActorHarness DENIES execution with ZERO
     checkpoint/dataset side effects
  5. trusted_operator=True executes as documented; policy+switch is rejected
  6. excessive budgets are CLAMPED to profile ceilings before execution;
     lowering below the ceiling still works
  7. the secret-scanner stub detects its defined patterns in payload/config
     (values, nested structures and keys) and fails loudly naming the field
  8. legitimate inputs still run end-to-end through an allowing policy
  9. SelfHealingPipeline refuses traversal video_ids before any write

Run:  python -m pytest tests/test_security_gates_sg2.py -v
      python tests/test_security_gates_sg2.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.policy.models import CAP_NETWORK_FETCH, Capability
from src.policy.evaluator import PolicyProfile, PolicyRule
from src.runtime import (
    AcquisitionActor,
    AcquisitionRuntime,
    ActorHarness,
    Outcome,
    PageResult,
    RunInput,
    RunOptions,
)
from src.runtime.context import RunContext, resolve_rooted
from src.pipeline.improve import PipelineMetrics, SelfHealingPipeline


def run(coro):
    return asyncio.run(coro)


class FakeActor(AcquisitionActor):
    provider_name = "fakeprovider"
    id_key = "item_id"
    actor_id = "acquisition.fake"
    actor_version = "1.0.0"

    def __init__(self, pages=None, caps=()):
        self.pages = pages if pages is not None else [
            [{"item_id": f"f_{i}", "text": f"item {i}"} for i in range(10)],
        ]
        self.caps = tuple(caps)
        self.calls: List[tuple] = []

    def capabilities(self):
        return self.caps or (CAP_NETWORK_FETCH,)

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        self.calls.append((cursor, page_index))
        idx = len(self.calls) - 1
        if idx < len(self.pages):
            pg = self.pages[idx]
            return PageResult(items=pg, next_cursor=cursor + len(pg),
                              has_more=idx < len(self.pages) - 1)
        return PageResult(items=[], next_cursor=cursor, has_more=False)


def allow_fake_profile(**kw) -> PolicyProfile:
    return PolicyProfile(
        name="allow-fake", version="1.0.0",
        rules=(PolicyRule(actor_id="acquisition.fake",
                          capability=CAP_NETWORK_FETCH.name),),
        **kw,
    )


def make_input(actor, url="https://example.test/target/1", **kw) -> RunInput:
    return RunInput(
        actor_id=actor.actor_id,
        actor_version=actor.actor_version,
        provider=kw.pop("provider", "fakeprovider"),
        target_url=url,
        payload=kw.pop("payload", {}),
        config=kw.pop("config", {}),
        **{k: v for k, v in kw.items() if k in ("job_id", "run_id")},
    )


# ── 1. Traversal / hostile identifiers rejected at RunInput ──────────────────

def test_traversal_job_id_rejected_naming_field():
    for hostile in ("../escape", "..", "a/../b", "/abs/path", r"..\\win",
                    "job id space", "UPPER_CASE"):
        try:
            RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                     provider="fakeprovider", target_url="https://x.test/",
                     job_id=hostile)
            raise AssertionError(f"job_id={hostile!r} must be rejected")
        except ValueError as e:
            assert "job_id" in str(e), f"error must name the field: {e}"


def test_traversal_run_id_rejected_naming_field():
    try:
        RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                 provider="fakeprovider", target_url="https://x.test/",
                 run_id="../../etc")
        raise AssertionError("traversal run_id must be rejected")
    except ValueError as e:
        assert "run_id" in str(e)


def test_serialized_hostile_ids_rejected_too():
    """from_dict cannot smuggle what the constructor rejects."""
    d = RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                 provider="fakeprovider", target_url="https://x.test/",
                 job_id="ok.id_1").to_dict()
    d["job_id"] = "../resurrected"
    try:
        RunInput.from_dict(d)
        raise AssertionError("from_dict must re-validate hostile ids")
    except ValueError as e:
        assert "job_id" in str(e)


# ── 2. Rooted-path enforcement at RunContext.create ──────────────────────────

def test_hostile_id_via_raw_runtime_path_rejected_before_write():
    """Defense in depth: a context built DIRECTLY (bypassing RunInput) still
    refuses hostile identifiers at BOTH layers — slug charset first, then
    rooted resolution — before any writer could exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for hostile in ("../escaped", "..\\windows", "/absolute/escape",
                        "sub/../../../../outside"):
            try:
                RunContext.create(
                    provider="fake", target_url="https://x.test/1",
                    job_id=hostile,
                    state_dir=str(Path(tmpdir) / "state"),
                    data_dir=str(Path(tmpdir) / "data"))
                raise AssertionError(f"job_id={hostile!r} must be refused")
            except ValueError:
                pass  # either layer rejecting is fail-closed correctness
        # nothing was written anywhere under tmpdir
        assert list(Path(tmpdir).rglob("*")) == []


def test_resolve_rooted_refuses_absolute_and_traversal_filenames():
    """The rooted-path assertion is the FINAL authority: even a filename that
    somehow passes upstream checks cannot move a write outside the root."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = str(Path(tmpdir) / "root")
        for hostile_name in ("../escape.json", "/etc/passwd",
                             "a/b/../../../outside.jsonl"):
            try:
                resolve_rooted(root, hostile_name, "checkpoint_path (state_dir)")
                raise AssertionError(f"filename {hostile_name!r} must be refused")
            except ValueError as e:
                assert "workspace root" in str(e), e
        # benign names resolve INSIDE the root
        ok = resolve_rooted(root, "job.ok.json", "state_dir")
        assert Path(ok).parent == Path(root).resolve().absolute()


# ── 3. Legitimate ids stay strictly rooted ────────────────────────────────────

def test_legitimate_checkpoint_and_dataset_remain_rooted():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir) / "state"
        data_root = Path(tmpdir) / "data"
        ctx = RunContext.create(provider="fake", target_url="https://x.test/1",
                                job_id="job.legit_1",
                                state_dir=str(state_root),
                                data_dir=str(data_root))
        assert Path(ctx.checkpoint_path) == (state_root.resolve().absolute()
                                             / "job.legit_1.json")
        assert Path(ctx.dataset_path) == (data_root.resolve().absolute()
                                          / "job.legit_1.jsonl")


# ── 4. Default harness DENIES with zero side effects ─────────────────────────

def test_default_harness_denies_without_policy_or_switch(tmp_path):
    actor = FakeActor(caps=(CAP_NETWORK_FETCH,))
    harness = ActorHarness(   # NO policy, NO trusted_operator switch
        state_dir=str(tmp_path / "state"),
        data_dir=str(tmp_path / "data"),
        print_fn=lambda *_: None,
    )
    summary = run(harness.run(actor, make_input(actor)))

    assert actor.calls == [], "default-deny must never execute the actor"
    assert summary.termination_reason == "policy_denied"
    assert summary.outcome == Outcome.POLICY_DENIED
    assert summary.items_written == 0 and summary.items_seen == 0
    assert summary.metrics["policy_decision"]["decision"] == "deny"
    assert summary.metrics["policy_decision"]["reason"] == "no_matching_rule"
    # ZERO filesystem mutation: rejection happened BEFORE any write
    assert not (tmp_path / "state").exists() or \
        list((tmp_path / "state").glob("*")) == []
    assert not (tmp_path / "data").exists() or \
        list((tmp_path / "data").glob("*")) == []


def test_default_harness_denies_zero_capability_actor(tmp_path):
    actor = FakeActor(caps=())
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None)
    summary = run(harness.run(actor, make_input(actor)))
    assert actor.calls == []
    assert summary.termination_reason == "policy_denied"


# ── 5. Explicit trusted-operator switch behaves as specified ────────────────

def test_trusted_operator_switch_executes_documented_mode(tmp_path):
    actor = FakeActor()
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None,
                           trusted_operator=True)
    summary = run(harness.run(actor, make_input(actor)))
    assert len(actor.calls) >= 1
    assert summary.outcome == Outcome.SUCCESS


def test_policy_plus_trusted_operator_is_rejected():
    try:
        ActorHarness(policy=allow_fake_profile(), trusted_operator=True)
        raise AssertionError("ambiguous configuration must fail closed")
    except ValueError as e:
        assert "ambiguous" in str(e)


# ── 6. Budget ceiling clamps ─────────────────────────────────────────────────

def test_excessive_config_budget_clamped_to_ceiling(tmp_path):
    profile = allow_fake_profile(max_items=5)
    actor = FakeActor()                       # would produce 10 items
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None,
                           policy=profile)
    inp = make_input(actor, config={"max_items": 1000})   # escalation attempt
    s = run(harness.run(actor, inp))
    assert s.items_seen == 5, "ceiling must clamp excessive config values"
    assert s.termination_reason == "max_comments_reached"


def test_excessive_explicit_options_clamped_too(tmp_path):
    profile = allow_fake_profile(max_items=4)
    actor = FakeActor()
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None,
                           policy=profile)
    s = run(harness.run(actor, make_input(actor),
                        options=RunOptions(max_items=999)))
    assert s.items_seen == 4, "explicit RunOptions are clamped alike"


def test_lowering_below_ceiling_still_honored(tmp_path):
    profile = allow_fake_profile(max_items=50)
    actor = FakeActor(pages=[[{"item_id": f"x_{i}"} for i in range(10)]])
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None,
                           policy=profile)
    s = run(harness.run(actor, make_input(actor,
                                          config={"max_items": 3})))
    assert s.items_seen == 3, "caller may lower below the ceiling"


# ── 7. Secret-scanner stub ───────────────────────────────────────────────────

def test_scanner_detects_defined_secret_patterns_in_payload():
    cases = [
        {"password": "hunter2secret"},                    # key match
        {"note": "password=hunter2secret"},               # assignment value
        {"nested": ["ok", {"token": "abcdef123456"}]},    # nested dict value
        {"auth": "Bearer abcdefghijklmnopqrst"},          # bearer header shape
        {"jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxInK.tag"},
        {"aws": "AKIAABCDEFGHIJKLMNOP"},
        {"gh": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2"},
        {"sk": "sk-" + "a1B2c3D4e5F6g7H8i9J0"},
    ]
    for payload in cases:
        try:
            RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                     provider="fakeprovider", target_url="https://x.test/",
                     payload=payload)
            raise AssertionError(f"payload {payload!r} must be refused")
        except ValueError as e:
            assert "payload" in str(e), f"error must name the field: {e}"
            assert "secret-like" in str(e).lower()


def test_scanner_detects_secrets_in_config():
    try:
        RunInput(actor_id="acquisition.fake", actor_version="1.0.0",
                 provider="fakeprovider", target_url="https://x.test/",
                 config={"sessionid": "180abc123def"})
        raise AssertionError("config secrets must be refused")
    except ValueError as e:
        assert "config" in str(e)


def test_legitimate_inputs_still_work_end_to_end(tmp_path):
    """Happy paths survive every gate: clean slugs, clean payload, sane budget."""
    profile = allow_fake_profile(max_items=100)
    actor = FakeActor()
    harness = ActorHarness(state_dir=str(tmp_path / "s"),
                           data_dir=str(tmp_path / "d"),
                           print_fn=lambda *_: None,
                           policy=profile)
    inp = make_input(actor, job_id="job.ok_1", run_id="run-9f0c",
                     payload={"video_id": "7673343206544706837"})
    s = run(harness.run(actor, inp))
    assert s.outcome == Outcome.SUCCESS
    ckpt = json.loads(Path(s.checkpoint_path).read_text())
    assert ckpt["target"]["video_id"] == "7673343206544706837"
    lines = [json.loads(l) for l in Path(s.dataset_path).read_text().splitlines() if l.strip()]
    assert len(lines) == 10


# ── 9. Improve loop refuses traversal ids before any write ───────────────────

def test_self_healing_pipeline_refuses_traversal_video_id(tmp_path):
    pipeline = SelfHealingPipeline(
        base_dir=tmp_path,
        collector=lambda *a, **k: [],
        processor=lambda obs, base, **k: PipelineMetrics(
            video_id="x", reported=0, captured=0, coverage=1.0,
            avg_quality=1.0, dup_rate=0.0, partial=False),
        max_iter=1, sleep_between=0,
        state_dir=tmp_path / "loops",
    )
    for hostile in ("../../escape", "bad id"):
        try:
            pipeline.run(hostile, "https://www.tiktok.com/@u/video/1")
            raise AssertionError(f"video_id={hostile!r} must be refused")
        except ValueError as e:
            assert "video_id" in str(e)
    # no loop-state file, no manifest was ever created for the hostile id
    assert list((tmp_path / "loops").glob("*")) == [] if (tmp_path / "loops").exists() else True
    assert not (tmp_path / "data" / "manifests").exists()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            if t.__code__.co_argcount:      # pytest-style tmp_path fixture
                import tempfile as _tf
                with _tf.TemporaryDirectory() as td:
                    t(Path(td))
            else:
                t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL: {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nSecurity-gate suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
