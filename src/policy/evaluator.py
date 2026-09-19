#!/usr/bin/env python3
"""
PolicyEvaluator v0 — deterministic, deny-by-default capability evaluation.

Contract (D-006, docs/architecture/POLICY-ARCHITECTURE.md):

    CapabilityRequest  →  PolicyEvaluator  →  ALLOW / DENY / REQUIRE_APPROVAL

Guarantees proven by tests (tests/test_policy_evaluator.py):

- DENY BY DEFAULT: no profile, no matching rule, malformed input, evaluator
  failure — every uncertain path resolves to DENY, never to an accidental
  ALLOW (Constitution §2/§3).
- DETERMINISTIC: pure rule matching over an immutable profile. No LLM, no
  network, no external service, no clock input to the decision itself.
- AUDITABLE: every evaluation returns a PolicyDecisionRecord carrying actor,
  capability, resource, decision, policy_version, reason and the matched
  rule id.
- UNTRUSTED INPUTS: request metadata / resource / purpose are NEVER granted
  on their own merit. Rules match on actor_id + capability (operator-authored
  policy), with optional resource (exact/prefix) and provider constraints.
  Provider comes from the trusted runtime context (RunInput.provider), never
  from request metadata.

This module decides whether an ACTION is permitted. It does NOT decide the
user's strategic objective — goal selection stays outside the runtime
(D-001). Stdlib only; no provider imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from src.policy.models import (
    CapabilityRequest,
    PolicyDecision,
    is_valid_identifier,
)

__all__ = [
    "PolicyRule",
    "PolicyProfile",
    "PolicyDecisionRecord",
    "PolicyEvaluator",
]


def _valid_version(value) -> bool:
    """Structural version check: short printable string without whitespace."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 64
        and value.strip() == value
        and len(value.split()) == 1
        and all(ch.isprintable() for ch in value)
    )


@dataclass(frozen=True)
class PolicyRule:
    """One explicit grant in a policy profile.

    A rule matches a request when ALL of its non-empty constraint axes match:
      - actor_id    : exact match (required, valid identifier)
      - capability  : exact capability-name match (required, valid identifier)
      - resource    : optional — resource_exact (equality) OR resource_prefix
                      (startswith). Setting both is rejected (ambiguous intent
                      fails closed at authoring time).
      - provider    : optional exact match against the TRUSTED provider
                      identity passed by the caller (RunContext/RunInput) —
                      never against actor-supplied metadata.

    decision is the verdict this rule yields on match: ALLOW or
    REQUIRE_APPROVAL. DENY rules are permitted for explicit audit clarity /
    carve-outs under first-match ordering, but they are never required —
    the default is already DENY.
    """

    actor_id: str
    capability: str
    decision: PolicyDecision = PolicyDecision.ALLOW
    resource_exact: str = ""
    resource_prefix: str = ""
    provider: str = ""
    rule_id: str = ""

    def __post_init__(self):
        if not is_valid_identifier(self.actor_id):
            raise ValueError(
                f"PolicyRule.actor_id must be a valid identifier, got {self.actor_id!r}"
            )
        if not is_valid_identifier(self.capability):
            raise ValueError(
                f"PolicyRule.capability must be a valid identifier, got {self.capability!r}"
            )
        if not isinstance(self.decision, PolicyDecision):
            raise ValueError(
                f"PolicyRule.decision must be a PolicyDecision, got {self.decision!r}"
            )
        if self.resource_exact and self.resource_prefix:
            raise ValueError(
                "PolicyRule: set resource_exact OR resource_prefix, not both "
                "(ambiguous constraint fails closed at authoring time)"
            )
        if not isinstance(self.resource_exact, str) or not isinstance(self.resource_prefix, str):
            raise ValueError("PolicyRule resource constraints must be strings")
        if self.provider and not is_valid_identifier(self.provider):
            raise ValueError(
                f"PolicyRule.provider must be a valid identifier, got {self.provider!r}"
            )
        if self.rule_id and not isinstance(self.rule_id, str):
            raise ValueError("PolicyRule.rule_id must be a string")

    @property
    def key(self) -> Tuple[str, str, str, str, str]:
        """Identity used for duplicate detection (author-bug rejection)."""
        return (
            self.actor_id,
            self.capability,
            self.resource_exact,
            self.resource_prefix,
            self.provider,
        )


@dataclass(frozen=True)
class PolicyProfile:
    """An immutable, versioned set of policy rules.

    Smallest reasonable representation: stdlib frozen dataclass, no
    YAML/JSON loading (SDD does not require it). An empty rules tuple is a
    valid deny-everything profile.

    S-G4 budget ceilings (threat model §3.D item 4): the optional ``max_*``
    axes cap the RunOptions budget values the harness will accept — a caller
    may LOWER a budget below its ceiling, never exceed it. ``None`` means no
    ceiling on that axis. Ceilings are enforced by the harness BEFORE
    execution; the evaluator itself does not consume them.
    """

    name: str
    version: str
    rules: Tuple[PolicyRule, ...] = ()
    max_items: Optional[int] = None
    max_pages: Optional[int] = None
    max_retries: Optional[int] = None
    max_empty_retries: Optional[int] = None
    max_stalls: Optional[int] = None
    max_parse_retries: Optional[int] = None

    _CEILING_AXES = (
        "max_items", "max_pages", "max_retries",
        "max_empty_retries", "max_stalls", "max_parse_retries",
    )

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"PolicyProfile.name must be a non-empty string, got {self.name!r}")
        if not _valid_version(self.version):
            raise ValueError(
                f"PolicyProfile.version must be a short non-empty version string "
                f"without whitespace, got {self.version!r}"
            )
        if not isinstance(self.rules, tuple) or not all(isinstance(r, PolicyRule) for r in self.rules):
            raise ValueError("PolicyProfile.rules must be a tuple of PolicyRule instances")
        for axis in self._CEILING_AXES:
            value = getattr(self, axis)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"PolicyProfile.{axis} ceiling must be an int >= 0 or None, "
                    f"got {value!r}"
                )
        seen = set()
        for rule in self.rules:
            if rule.key in seen:
                raise ValueError(
                    f"PolicyProfile contains duplicate rule constraint set {rule.key!r} "
                    "(a duplicated rule is an author bug)"
                )
            seen.add(rule.key)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "version": self.version,
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "actor_id": r.actor_id,
                    "capability": r.capability,
                    "decision": r.decision.value,
                    "resource_exact": r.resource_exact,
                    "resource_prefix": r.resource_prefix,
                    "provider": r.provider,
                }
                for r in self.rules
            ],
        }
        # Additive S-G4 ceilings (legacy readers ignore unknown keys).
        for axis in self._CEILING_AXES:
            d[axis] = getattr(self, axis)
        return d


@dataclass(frozen=True)
class PolicyDecisionRecord:
    """The auditable result of ONE policy evaluation.

    policy_version is "" only when no policy was configured at all (which is
    itself a DENY). evaluated_at is audit metadata — it never participates in
    the decision.
    """

    actor_id: str
    capability: str
    resource: str
    decision: PolicyDecision
    policy_version: str
    reason: str
    matched_rule_id: str = ""
    evaluated_at: str = field(default_factory=lambda: _utc_now())

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "capability": self.capability,
            "resource": self.resource,
            "decision": self.decision.value,
            "policy_version": self.policy_version,
            "reason": self.reason,
            "matched_rule_id": self.matched_rule_id,
            "evaluated_at": self.evaluated_at,
        }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _rule_matches(rule: PolicyRule, request: CapabilityRequest, provider: str) -> bool:
    if rule.actor_id != request.actor_id:
        return False
    if rule.capability != request.capability_name:
        return False
    if rule.provider and rule.provider != provider:
        return False
    if rule.resource_exact and request.resource != rule.resource_exact:
        return False
    if rule.resource_prefix and not request.resource.startswith(rule.resource_prefix):
        return False
    return True


class PolicyEvaluator:
    """Pure, deterministic request → decision function over one profile.

    Fail-closed everywhere (Constitution §3): any validation problem,
    malformed input, or internal error resolves to a DENY record — never an
    exception escaping toward the caller with an implicit "allow", and never
    an accidental ALLOW.
    """

    def __init__(self, profile: Optional[PolicyProfile]):
        self._profile = profile

    @property
    def policy_version(self) -> str:
        return self._profile.version if self._profile is not None else ""

    def evaluate(self, request, provider: str = "") -> PolicyDecisionRecord:
        try:
            return self._evaluate(request, provider)
        except Exception as e:  # noqa: BLE001 — fail closed on ANY internal error
            return self._deny(
                request,
                reason=f"evaluator_error: {type(e).__name__}",
                policy_version=self.policy_version,
            )

    # ── internals ─────────────────────────────────────────────────────────
    def _deny(self, request, reason: str, policy_version: str,
              matched_rule_id: str = "") -> PolicyDecisionRecord:
        actor_id = getattr(request, "actor_id", "") if isinstance(request, CapabilityRequest) else ""
        capability = (
            getattr(request, "capability_name", "") if isinstance(request, CapabilityRequest) else ""
        )
        resource = getattr(request, "resource", "") if isinstance(request, CapabilityRequest) else ""
        return PolicyDecisionRecord(
            actor_id=str(actor_id)[:128],
            capability=str(capability)[:128],
            resource=str(resource)[:256],
            decision=PolicyDecision.DENY,
            policy_version=policy_version,
            reason=reason,
            matched_rule_id=matched_rule_id,
        )

    def _evaluate(self, request, provider: str) -> PolicyDecisionRecord:
        # 1. Missing policy → DENY (requirement 6).
        if self._profile is None:
            return self._deny(request, reason="no_policy_configured", policy_version="")

        # 2. Malformed request → DENY, never an accidental ALLOW (requirement 7).
        if not isinstance(request, CapabilityRequest):
            return self._deny(request, reason="malformed_request: not a CapabilityRequest",
                              policy_version=self._profile.version)

        # 3. Missing / structurally invalid actor identity → DENY (requirement 5).
        #    CapabilityRequest already refuses empty actor_id at construction;
        #    this re-check also covers identifier-format violations.
        if not is_valid_identifier(request.actor_id):
            return self._deny(request, reason="invalid_actor_identity",
                              policy_version=self._profile.version)
        if not is_valid_identifier(request.capability_name):
            return self._deny(request, reason="invalid_capability_name",
                              policy_version=self._profile.version)

        # 4. First matching rule wins — deterministic (declaration order).
        for index, rule in enumerate(self._profile.rules):
            if _rule_matches(rule, request, provider):
                return PolicyDecisionRecord(
                    actor_id=request.actor_id,
                    capability=request.capability_name,
                    resource=request.resource,
                    decision=rule.decision,
                    policy_version=self._profile.version,
                    reason=(
                        f"matched rule {index}"
                        + (f" ({rule.rule_id})" if rule.rule_id else "")
                    ),
                    matched_rule_id=rule.rule_id or f"rules[{index}]",
                )

        # 5. Unknown capability / no matching rule → DENY (requirements 2, 4).
        return self._deny(
            request,
            reason="no_matching_rule",
            policy_version=self._profile.version,
        )
