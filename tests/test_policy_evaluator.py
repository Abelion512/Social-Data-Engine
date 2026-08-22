#!/usr/bin/env python3
"""
Deterministic tests for PolicyEvaluator v0 + the ActorHarness policy gate.

Proves (stdlib only, no browser, no network, no LLM):

  A. Evaluator contract (deny-by-default)
   1. known allowed capability → ALLOW (auditable record)
   2. unknown capability → DENY
   3. actor mismatch → DENY
   4. missing policy → DENY (policy_version "")
   5. malformed request → no accidental ALLOW
   6. REQUIRE_APPROVAL is representable
   7. changing policy version changes recorded version
   8. same request + same policy → same decision (determinism)
   9. evaluator internal error → DENY (fail closed)
  10. resource / provider constraints: match and mismatch paths

  B. Harness gate (enforcement BEFORE execution)
  11. protected action NOT executed after DENY; no checkpoint/dataset files
  12. an actor cannot bypass by declaring extra capabilities
  13. zero-capability actor: runs without policy (trusted-operator mode),
      DENIED under enforced policy
  14. duplicate declarations still rejected (structural layer unchanged)
  15. empty actor_id cannot construct a request; invalid actor id → DENY
  16. TikTok-shaped actor + explicitly-allowing policy → behavior UNCHANGED,
      policy_version stamped into checkpoint provenance

Run:  python -m pytest tests/test_policy_evaluator.py -v
      python tests/test_policy_evaluator.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.policy.models import (
    CAP_BROWSER_AUTOMATE,
    CAP_FILESYSTEM_WRITE,
    CAP_NETWORK_FETCH,
    Capability,
    CapabilityRequest,
    PolicyDecision,
)
from src.policy.evaluator import (
    PolicyDecisionRecord,
    PolicyEvaluator,
    PolicyProfile,
    PolicyRule,
)
from src.runtime import (
    AcquisitionActor,
    AcquisitionRuntime,
    ActorHarness,
    Outcome,
    PageResult,
    RunInput,
    RunLifecycle,
    classify_termination,
)
from src.providers.tiktok_actor import TikTokAcquisitionActor


def run(coro):
    return asyncio.run(coro)


# ── fixtures ──────────────────────────────────────────────────────────────────

ACTOR = "acquisition.tiktok"
OTHER_ACTOR = "acquisition.other"


def allow_rule(**kw) -> PolicyRule:
    defaults = dict(actor_id=ACTOR, capability=CAP_NETWORK_FETCH.name)
    defaults.update(kw)
    return PolicyRule(**defaults)


def basic_profile(version="1.0.0", rules=None) -> PolicyProfile:
    return PolicyProfile(
        name="test-profile",
        version=version,
        rules=rules if rules is not None else (allow_rule(),),
    )


def request(capability=CAP_NETWORK_FETCH, actor_id=ACTOR,
            resource="https://example.test/@u/video/1") -> CapabilityRequest:
    return CapabilityRequest.for_capability(
        capability, actor_id=actor_id, resource=resource)


class FakeActor(AcquisitionActor):
    provider_name = "fakeprovider"
    id_key = "item_id"
    actor_id = "acquisition.fake"
    actor_version = "1.0.0"

    def __init__(self, caps=()):
        self.caps = tuple(caps)
        self.calls: List[tuple] = []

    def capabilities(self):
        return self.caps

    async def fetch_page(self, ctx, cursor, page_index) -> PageResult:
        self.calls.append((cursor, page_index))
        if page_index == 0:
            return PageResult(items=[{"item_id": "a", "text": "x"}],
                              next_cursor=1, has_more=False)
        return PageResult(items=[], next_cursor=cursor, has_more=False)


def make_harness(tmpdir: Path, policy=None) -> ActorHarness:
    return ActorHarness(
        state_dir=str(tmpdir / "state"),
        data_dir=str(tmpdir / "data"),
        print_fn=lambda *_: None,
        policy=policy,
    )


def make_input(actor, url="https://example.test/target/1", **kw) -> RunInput:
    return RunInput(
        actor_id=actor.actor_id,
        actor_version=actor.actor_version,
        provider=kw.pop("provider", "fakeprovider"),
        target_url=url,
        **kw,
    )


# ══ A. Evaluator contract ═════════════════════════════════════════════════════

def test_1_known_allowed_capability_is_allowed_with_audit_record():
    rec = PolicyEvaluator(basic_profile()).evaluate(request())
    assert isinstance(rec, PolicyDecisionRecord)
    assert rec.decision is PolicyDecision.ALLOW
    # auditable decision object: actor, capability, decision, policy_version, reason
    assert rec.actor_id == ACTOR
    assert rec.capability == CAP_NETWORK_FETCH.name
    assert rec.policy_version == "1.0.0"
    assert rec.reason and rec.matched_rule_id
    d = rec.to_dict()
    assert d["decision"] == "allow" and d["policy_version"] == "1.0.0"


def test_2_unknown_capability_is_denied():
    rec = PolicyEvaluator(basic_profile()).evaluate(request(CAP_FILESYSTEM_WRITE))
    assert rec.decision is PolicyDecision.DENY
    assert rec.reason == "no_matching_rule"
    assert rec.policy_version == "1.0.0"  # denial still records which policy evaluated


def test_3_actor_mismatch_is_denied():
    rec = PolicyEvaluator(basic_profile()).evaluate(request(actor_id=OTHER_ACTOR))
    assert rec.decision is PolicyDecision.DENY


def test_4_missing_policy_denies_and_records_empty_version():
    rec = PolicyEvaluator(None).evaluate(request())
    assert rec.decision is PolicyDecision.DENY
    assert rec.reason == "no_policy_configured"
    assert rec.policy_version == ""
    # an EMPTY profile (valid but rule-less) also denies everything
    empty = PolicyProfile(name="deny-all", version="0.0.1", rules=())
    rec2 = PolicyEvaluator(empty).evaluate(request())
    assert rec2.decision is PolicyDecision.DENY
    assert rec2.policy_version == "0.0.1"


def test_5_malformed_request_never_allows():
    ev = PolicyEvaluator(basic_profile())
    # non-CapabilityRequest objects deny instead of raising toward the caller
    for bad in (None, "network.fetch", {"actor_id": ACTOR}, 42):
        rec = ev.evaluate(bad)
        assert rec.decision is PolicyDecision.DENY, f"accidental ALLOW for {bad!r}"
    # a structurally-invalid actor identity in a request denies
    rec = ev.evaluate(request(actor_id="BAD ACTOR"))
    assert rec.decision is PolicyDecision.DENY
    assert rec.reason == "invalid_actor_identity"
    # empty actor_id cannot even construct a request — explicit validation error
    try:
        CapabilityRequest.for_capability(CAP_NETWORK_FETCH, actor_id="")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_6_require_approval_is_representable():
    profile = PolicyProfile(
        name="gated", version="2.0.0",
        rules=(PolicyRule(actor_id=ACTOR, capability=CAP_BROWSER_AUTOMATE.name,
                          decision=PolicyDecision.REQUIRE_APPROVAL),),
    )
    rec = PolicyEvaluator(profile).evaluate(request(CAP_BROWSER_AUTOMATE))
    assert rec.decision is PolicyDecision.REQUIRE_APPROVAL
    assert rec.policy_version == "2.0.0"


def test_7_changing_policy_version_changes_recorded_version():
    req = request()
    v1 = PolicyEvaluator(basic_profile("1.0.0")).evaluate(req)
    v2 = PolicyEvaluator(basic_profile("9.9.9")).evaluate(req)
    assert v1.decision is v2.decision is PolicyDecision.ALLOW
    assert v1.policy_version == "1.0.0"
    assert v2.policy_version == "9.9.9"


def test_8_deterministic_same_request_same_policy_same_decision():
    ev = PolicyEvaluator(basic_profile())
    a = [ev.evaluate(request()) for _ in range(3)]
    b = PolicyEvaluator(basic_profile()).evaluate(request())
    for rec in a + [b]:
        # decision-relevant fields identical (evaluated_at is audit metadata only)
        assert (rec.decision, rec.reason, rec.matched_rule_id,
                rec.policy_version) == (
            a[0].decision, a[0].reason, a[0].matched_rule_id, a[0].policy_version)


def test_9_evaluator_internal_error_fails_closed_to_deny():
    class _CorruptProfile(PolicyProfile):
        """Simulates corrupted policy STATE: reading the rule store raises.

        Subclass-based fault injection (no class monkey-patching): overrides
        __init__ so the dataclass-generated initializer/__post_init__ never
        runs, then makes ``rules`` itself the fault.
        """

        def __init__(self):
            # frozen dataclass ⇒ stamp identity fields via object.__setattr__
            object.__setattr__(self, "name", "corrupt")
            object.__setattr__(self, "version", "1.0.0")

        @property
        def rules(self):
            raise RuntimeError("corrupt policy state")

    rec = PolicyEvaluator(_CorruptProfile()).evaluate(request())
    assert isinstance(rec, PolicyDecisionRecord)
    assert rec.decision is PolicyDecision.DENY
    assert rec.reason.startswith("evaluator_error")
    assert rec.policy_version == "1.0.0"


def test_10_resource_and_provider_constraints():
    url = "https://www.tiktok.com/@user/video/123"

    exact = PolicyProfile(name="p", version="1",
                          rules=(allow_rule(resource_exact=url),))
    ok = PolicyEvaluator(exact).evaluate(request(resource=url))
    miss = PolicyEvaluator(exact).evaluate(request(resource=url + "/other"))
    assert ok.decision is PolicyDecision.ALLOW
    assert miss.decision is PolicyDecision.DENY  # resource mismatch

    prefix = PolicyProfile(name="p", version="1",
                           rules=(allow_rule(resource_prefix="https://www.tiktok.com/"),))
    assert PolicyEvaluator(prefix).evaluate(request(resource=url)).decision is PolicyDecision.ALLOW
    assert PolicyEvaluator(prefix).evaluate(
        request(resource="https://evil.test/@user/video/123")
    ).decision is PolicyDecision.DENY

    prov = PolicyProfile(name="p", version="1",
                         rules=(allow_rule(provider="tiktok"),))
    assert PolicyEvaluator(prov).evaluate(request(), provider="tiktok").decision is PolicyDecision.ALLOW
    # provider constraint comes from TRUSTED runtime context — mismatch denies
    assert PolicyEvaluator(prov).evaluate(request(), provider="linkedin").decision is PolicyDecision.DENY
    # actor-supplied metadata can NEVER satisfy a provider/resource constraint
    sneaky = CapabilityRequest.for_capability(
        CAP_NETWORK_FETCH, actor_id=ACTOR, resource="https://evil.test/x",
        metadata={"provider": "tiktok", "resource": url})
    assert PolicyEvaluator(prov).evaluate(sneaky, provider="linkedin").decision is PolicyDecision.DENY


def test_10b_invalid_rules_and_profiles_fail_at_authoring_time():
    def raises(fn):
        try:
            fn()
            return False
        except ValueError:
            return True

    assert raises(lambda: PolicyRule(actor_id="BAD ID", capability="network.fetch"))
    assert raises(lambda: PolicyRule(actor_id=ACTOR, capability="NOT VALID"))
    assert raises(lambda: PolicyRule(actor_id=ACTOR, capability="network.fetch",
                                     decision="allow"))  # type: ignore[arg-type]
    assert raises(lambda: PolicyRule(actor_id=ACTOR, capability="network.fetch",
                                     resource_exact="/a", resource_prefix="/b"))
    assert raises(lambda: PolicyProfile(name="", version="1"))
    assert raises(lambda: PolicyProfile(name="p", version="BAD VERSION"))
    dup = dict(actor_id=ACTOR, capability="network.fetch")
    assert raises(lambda: PolicyProfile(name="p", version="1", rules=(
        PolicyRule(rule_id="a", **dup), PolicyRule(rule_id="b", **dup))))


# ══ B. Harness gate ═══════════════════════════════════════════════════════════

def test_11_deny_stops_execution_before_protected_action_no_side_effects(tmp_path):
    actor = FakeActor(caps=(CAP_NETWORK_FETCH,))
    harness = make_harness(tmp_path, policy=basic_profile())  # allows acquisition.tiktok ONLY
    summary = run(harness.run(actor, make_input(actor)))

    # protected action never ran
    assert actor.calls == []
    # explicit additive termination surface
    assert summary.termination_reason == "policy_denied"
    assert summary.outcome == classify_termination("policy_denied") == Outcome.POLICY_DENIED
    assert summary.lifecycle_state == RunLifecycle.TERMINATED
    assert summary.items_written == 0 and summary.items_seen == 0
    # audited decision rides the summary
    assert summary.metrics["policy_decision"]["decision"] == "deny"
    # ZERO side effects on disk: no checkpoint, no dataset
    assert list((tmp_path / "state").glob("*")) == []
    assert list((tmp_path / "data").glob("*")) == []


def test_11b_allow_runs_the_action(tmp_path):
    profile = PolicyProfile(name="fake-ok", version="1.0.0", rules=(
        PolicyRule(actor_id="acquisition.fake", capability=CAP_NETWORK_FETCH.name),))
    actor = FakeActor(caps=(CAP_NETWORK_FETCH,))
    harness = make_harness(tmp_path, policy=profile)
    summary = run(harness.run(actor, make_input(actor)))
    assert len(actor.calls) >= 1, "allowed run must execute the protected action"
    assert summary.termination_reason in ("finished", "has_more_false")
    assert summary.outcome == Outcome.SUCCESS


def test_12_extra_declared_capabilities_cannot_bypass_evaluation(tmp_path):
    # policy allows network.fetch for this actor — but the actor ALSO declares
    # filesystem.write, which has NO matching rule ⇒ whole run denied.
    profile = PolicyProfile(name="partial", version="1.0.0", rules=(
        PolicyRule(actor_id="acquisition.fake", capability=CAP_NETWORK_FETCH.name),))
    actor = FakeActor(caps=(CAP_NETWORK_FETCH, CAP_FILESYSTEM_WRITE))
    harness = make_harness(tmp_path, policy=profile)
    summary = run(harness.run(actor, make_input(actor)))
    assert actor.calls == []
    assert summary.termination_reason == "policy_denied"
    assert summary.metrics["policy_decision"]["capability"] == CAP_FILESYSTEM_WRITE.name


def test_13_zero_capability_actor(tmp_path):
    actor = FakeActor(caps=())

    # trusted-operator mode (no policy): behavior UNCHANGED — still executes
    harness_open = make_harness(tmp_path / "open")
    s_open = run(harness_open.run(actor, make_input(actor)))
    assert len(actor.calls) >= 1
    assert s_open.termination_reason in ("finished", "has_more_false")

    # enforced policy: declaring nothing grants nothing → DENY
    actor2 = FakeActor(caps=())
    harness_gated = make_harness(tmp_path / "gated", policy=basic_profile())
    s_gate = run(harness_gated.run(actor2, make_input(actor2)))
    assert actor2.calls == []
    assert s_gate.termination_reason == "policy_denied"
    assert s_gate.metrics["policy_decision"]["reason"] == \
        "no_capabilities_declared_under_enforced_policy"


def test_14_duplicate_declarations_still_rejected_structurally(tmp_path):
    from src.runtime.harness import ActorHarness as H  # structural check unchanged
    actor = FakeActor(caps=(CAP_NETWORK_FETCH, CAP_NETWORK_FETCH))
    try:
        H.validate_declared_capabilities(actor)
        raised = False
    except ValueError:
        raised = True
    assert raised
    # duplicate RULES are likewise rejected at authoring time (test_10b);
    # here we prove the harness never even reaches evaluation with dupes.


def test_15_require_approval_gates_the_run_without_an_approval_workflow(tmp_path):
    profile = PolicyProfile(name="gate", version="1.0.0", rules=(
        PolicyRule(actor_id="acquisition.fake",
                   capability=CAP_NETWORK_FETCH.name,
                   decision=PolicyDecision.REQUIRE_APPROVAL),))
    actor = FakeActor(caps=(CAP_NETWORK_FETCH,))
    harness = make_harness(tmp_path, policy=profile)
    summary = run(harness.run(actor, make_input(actor)))
    assert actor.calls == []  # approval workflow does not exist yet — run stops
    assert summary.termination_reason == "approval_required"
    assert summary.outcome == Outcome.APPROVAL_REQUIRED
    assert summary.lifecycle_state == RunLifecycle.TERMINATED
    assert summary.metrics["policy_decision"]["decision"] == "require_approval"


def test_16_tiktok_actor_behavior_unchanged_under_explicitly_allowing_policy(tmp_path):
    """Existing TikTok path keeps working when an explicit allow-policy is supplied."""
    pages = [{"comments": [{"comment_id": str(100 + i), "text": f"c{i}"} for i in range(5)],
              "cursor": 5, "has_more": 0}]

    class RecordedSource:
        def __init__(self):
            self.calls = []

        async def __call__(self, cursor, page_index=0):
            self.calls.append((cursor, page_index))
            return pages[0] if page_index == 0 else {"comments": [], "cursor": cursor, "has_more": 0}

    source = RecordedSource()
    actor = TikTokAcquisitionActor(page_source=source)

    tiktok_allow = PolicyProfile(name="tiktok-allow", version="3.1.4", rules=(
        PolicyRule(actor_id="acquisition.tiktok", capability=CAP_NETWORK_FETCH.name),
        PolicyRule(actor_id="acquisition.tiktok", capability=CAP_BROWSER_AUTOMATE.name),
    ))
    harness = make_harness(tmp_path, policy=tiktok_allow)
    run_input = make_input(
        actor, url="https://www.tiktok.com/@user/video/42", provider="tiktok")
    summary = run(harness.run(actor, run_input))

    assert len(source.calls) >= 1, "explicitly-allowed TikTok run must execute unchanged"
    assert summary.actor_id == "acquisition.tiktok"
    assert summary.items_written == 5
    assert summary.termination_reason in ("finished", "has_more_false")
    assert summary.outcome == Outcome.SUCCESS
    # policy version stamped into checkpoint provenance (audit trail)
    ckpt_file = tmp_path / "state" / f"{summary.job_id}.json"
    assert ckpt_file.exists()
    target = json.loads(ckpt_file.read_text()).get("target", {})
    assert target.get("policy_version") == "3.1.4"
    assert set(target.get("capabilities", [])) == {
        CAP_NETWORK_FETCH.name, CAP_BROWSER_AUTOMATE.name}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if name in ("test_11_deny_stops_execution_before_protected_action_no_side_effects",
                            "test_11b_allow_runs_the_action",
                            "test_12_extra_declared_capabilities_cannot_bypass_evaluation",
                            "test_13_zero_capability_actor",
                            "test_14_duplicate_declarations_still_rejected_structurally",
                            "test_15_require_approval_gates_the_run_without_an_approval_workflow",
                            "test_16_tiktok_actor_behavior_unchanged_under_explicitly_allowing_policy"):
                    import tempfile
                    with tempfile.TemporaryDirectory() as td:
                        fn(Path(td))
                else:
                    fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{'ALL GREEN' if failures == 0 else f'{failures} FAILURES'}")
    sys.exit(1 if failures else 0)
