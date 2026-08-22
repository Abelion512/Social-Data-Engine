#!/usr/bin/env python3
"""
Policy layer — machine-readable contract + deterministic evaluator.

Models (models.py): Capability, CapabilityRequest, PolicyDecision,
ExecutionBudget — the shared vocabulary. No second capability system exists.

Evaluator (evaluator.py): PolicyEvaluator v0 — deterministic,
deny-by-default evaluation of CapabilityRequest → PolicyDecision over an
immutable, versioned PolicyProfile. Every uncertain path (missing policy,
unknown capability, malformed request, evaluator error) resolves to DENY.
See docs/architecture/POLICY-ARCHITECTURE.md and ENGINEERING_CONSTITUTION.md.

Stdlib only. No provider imports. No LLM, no network, no external service.
"""
from src.policy.models import (
    CAP_BROWSER_AUTOMATE,
    CAP_FILESYSTEM_READ,
    CAP_FILESYSTEM_WRITE,
    CAP_NETWORK_FETCH,
    CAP_PROCESS_EXECUTE,
    POLICY_MODEL_VERSION,
    Capability,
    CapabilityRequest,
    ExecutionBudget,
    PolicyDecision,
)
from src.policy.evaluator import (
    PolicyDecisionRecord,
    PolicyEvaluator,
    PolicyProfile,
    PolicyRule,
)

__all__ = [
    "CAP_BROWSER_AUTOMATE",
    "CAP_FILESYSTEM_READ",
    "CAP_FILESYSTEM_WRITE",
    "CAP_NETWORK_FETCH",
    "CAP_PROCESS_EXECUTE",
    "POLICY_MODEL_VERSION",
    "Capability",
    "CapabilityRequest",
    "ExecutionBudget",
    "PolicyDecision",
    "PolicyDecisionRecord",
    "PolicyEvaluator",
    "PolicyProfile",
    "PolicyRule",
]
