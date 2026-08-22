#!/usr/bin/env python3
"""Tests for the policy model CONTRACT only (src/policy/models.py).

These tests prove serialization, enum states, validation, and versioning.
They deliberately do NOT prove any enforcement: no evaluator exists yet.
See docs/architecture/POLICY-ARCHITECTURE.md — enforcement is PLANNED.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.policy import (
    POLICY_MODEL_VERSION,
    CAP_BROWSER_AUTOMATE,
    CAP_NETWORK_FETCH,
    Capability,
    CapabilityRequest,
    ExecutionBudget,
    PolicyDecision,
)


def _raises_value_error(fn):
    try:
        fn()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def test_capability_can_be_represented():
    cap = Capability(name="network.fetch", description="fetch remote data")
    assert cap.name == "network.fetch"
    # known vocabulary constants are valid capabilities
    assert CAP_NETWORK_FETCH.name == "network.fetch"
    assert CAP_BROWSER_AUTOMATE.name == "browser.automate"
    # serializable round-trip
    assert Capability.from_dict(cap.to_dict()) == cap
    # frozen: immutable contract
    try:
        cap.name = "x"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "Capability must be frozen"
    # invalid names rejected
    assert _raises_value_error(lambda: Capability(name=""))
    assert _raises_value_error(lambda: Capability(name="BAD SPACE"))
    assert _raises_value_error(lambda: Capability(name=123))  # type: ignore[arg-type]


def test_policy_decision_states():
    # exactly ALLOW / DENY / REQUIRE_APPROVAL
    assert {d.name for d in PolicyDecision} == {"ALLOW", "DENY", "REQUIRE_APPROVAL"}
    assert len(PolicyDecision) == 3
    assert PolicyDecision("allow") is PolicyDecision.ALLOW
    assert PolicyDecision("deny") is PolicyDecision.DENY
    assert PolicyDecision("require_approval") is PolicyDecision.REQUIRE_APPROVAL
    # unknown verdicts are unrepresentable
    try:
        PolicyDecision("maybe")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_capability_request_serializable():
    req = CapabilityRequest.for_capability(
        CAP_NETWORK_FETCH,
        actor_id="acquisition.tiktok",
        resource="https://example.tiktok/@user/video/1",
        purpose="research-collection",
        metadata={"session": "cookie-based"},
    )
    d = req.to_dict()
    assert d["actor_id"] == "acquisition.tiktok"
    assert d["capability_name"] == "network.fetch"
    # round-trip preserves every field
    rt = CapabilityRequest.from_dict(d)
    assert rt == req
    # minimal construction also valid and serializable
    mini = CapabilityRequest(actor_id="harness.a", capability_name="filesystem.read")
    assert CapabilityRequest.from_dict(mini.to_dict()) == mini
    # required context enforced
    assert _raises_value_error(lambda: CapabilityRequest(actor_id="", capability_name="network.fetch"))
    assert _raises_value_error(lambda: CapabilityRequest(actor_id="a", capability_name=""))


def test_execution_budget_serializable():
    b = ExecutionBudget(max_iterations=10, max_items=500)
    d = b.to_dict()
    assert d["max_items"] == 500
    assert d["max_runtime_seconds"] is None  # unset axes stay explicitly None
    assert ExecutionBudget.from_dict(d) == b
    # full-axis budget round-trips too
    full = ExecutionBudget(
        max_iterations=3,
        max_runtime_seconds=600,
        max_items=1000,
        max_pages=50,
        max_retries=2,
        max_network_calls=400,
    )
    assert ExecutionBudget.from_dict(full.to_dict()) == full


def test_execution_budget_bounded_values():
    b = ExecutionBudget(
        max_iterations=5,
        max_runtime_seconds=300,
        max_items=2000,
        max_pages=100,
        max_retries=3,
        max_network_calls=250,
    )
    axes = b.to_dict()
    expected = {
        "max_iterations": 5,
        "max_runtime_seconds": 300,
        "max_items": 2000,
        "max_pages": 100,
        "max_retries": 3,
        "max_network_calls": 250,
    }
    for axis, value in expected.items():
        assert axes[axis] == value, f"{axis} not represented"


def test_policy_version_recorded():
    assert isinstance(POLICY_MODEL_VERSION, str) and POLICY_MODEL_VERSION.count(".") == 2
    for payload in (
        Capability(name="network.fetch").to_dict(),
        CapabilityRequest(actor_id="a", capability_name="c").to_dict(),
        ExecutionBudget(max_items=1).to_dict(),
    ):
        assert payload.get("policy_model_version") == POLICY_MODEL_VERSION, (
            f"serialized payload missing version stamp: {payload}"
        )


def test_no_secret_fields_of_the_model():
    secret_markers = ("secret", "token", "password", "credential", "api_key", "cookie")
    payloads = [
        Capability(name="network.fetch").to_dict(),
        CapabilityRequest(actor_id="a", capability_name="c").to_dict(),
        ExecutionBudget(max_items=1).to_dict(),
    ]
    for payload in payloads:
        for key in payload:
            assert not any(m in key.lower() for m in secret_markers), (
                f"policy models must never carry secret-like fields, found: {key}"
            )


def test_invalid_budgets_rejected():
    # negative bounds rejected on every axis
    assert _raises_value_error(lambda: ExecutionBudget(max_items=-1))
    assert _raises_value_error(lambda: ExecutionBudget(max_iterations=-5))
    assert _raises_value_error(lambda: ExecutionBudget(max_runtime_seconds=-1))
    assert _raises_value_error(lambda: ExecutionBudget(max_pages=-2))
    assert _raises_value_error(lambda: ExecutionBudget(max_retries=-1))
    assert _raises_value_error(lambda: ExecutionBudget(max_network_calls=-3))
    # wrong types rejected (bool is not an acceptable bound; neither is str)
    assert _raises_value_error(lambda: ExecutionBudget(max_items=True))
    assert _raises_value_error(lambda: ExecutionBudget(max_items="100"))  # type: ignore[arg-type]
    # fully unbounded budget rejected — unbounded execution is unrepresentable
    assert _raises_value_error(lambda: ExecutionBudget())
    # zero IS a valid bound (this axis permits nothing)
    assert ExecutionBudget(max_items=0).max_items == 0


if __name__ == "__main__":
    tests = [
        test_capability_can_be_represented,
        test_policy_decision_states,
        test_capability_request_serializable,
        test_execution_budget_serializable,
        test_execution_budget_bounded_values,
        test_policy_version_recorded,
        test_no_secret_fields_of_the_model,
        test_invalid_budgets_rejected,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    total = len(tests)
    print(f"\nPolicy Model Suite: {total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
