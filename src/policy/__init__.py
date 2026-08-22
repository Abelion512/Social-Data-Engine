#!/usr/bin/env python3
"""
Policy vocabulary — machine-readable contract ONLY.

This package defines the data models for future policy enforcement:
Capability, CapabilityRequest, PolicyDecision, ExecutionBudget.

There is NO evaluator here. Nothing in this package grants, denies, or
enforces anything at runtime. See docs/architecture/POLICY-ARCHITECTURE.md
for where enforcement will integrate, and ENGINEERING_CONSTITUTION.md for
the principles these models serve.

Stdlib only. Every model is serializable (to_dict/from_dict round-trip) and
stamps POLICY_MODEL_VERSION into its serialized form.
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
]
