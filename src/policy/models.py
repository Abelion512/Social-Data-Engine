#!/usr/bin/env python3
"""
Policy contract models — dataclasses + enums, stdlib only.

Contract rules:
- Serializable: every model round-trips through to_dict()/from_dict().
- Versioned: serialized payloads carry POLICY_MODEL_VERSION.
- No secrets: no field of any model may hold credentials/tokens; validation
  is structural only (names, bounds) — never secret material.
- Validation is fail-closed: invalid instances cannot be constructed.

This module intentionally contains NO evaluation logic. A DENY here is just
an enum value — nothing consumes it yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

POLICY_MODEL_VERSION = "1.0.0"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PolicyDecision(Enum):
    """The complete set of policy verdicts. Evaluators add metadata, never new verdicts."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class Capability:
    """A namespaced action verb an actor/harness may request.

    Names are lowercase, dot-namespaced, opaque to the runtime
    (e.g. "network.fetch", "filesystem.write", "browser.automate").
    """

    name: str
    description: str = ""

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError(
                f"invalid capability name: {self.name!r} "
                "(expected lowercase dot-namespaced identifier)"
            )

    def to_dict(self) -> dict:
        return {
            "policy_model_version": POLICY_MODEL_VERSION,
            "name": self.name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Capability":
        return cls(name=d["name"], description=d.get("description", ""))


# Known capability vocabulary (seed constants, not exhaustive — providers may declare more).
CAP_NETWORK_FETCH = Capability("network.fetch", "Retrieve data from an external endpoint")
CAP_FILESYSTEM_READ = Capability("filesystem.read", "Read files within declared boundaries")
CAP_FILESYSTEM_WRITE = Capability("filesystem.write", "Write files within declared boundaries")
CAP_PROCESS_EXECUTE = Capability("process.execute", "Spawn a subprocess (restricted class)")
CAP_BROWSER_AUTOMATE = Capability("browser.automate", "Drive a browser session")


@dataclass(frozen=True)
class CapabilityRequest:
    """The context necessary to evaluate one capability request — no more."""

    actor_id: str
    capability_name: str
    resource: str = ""          # opaque target descriptor (URL/path); policy decides how to treat it
    purpose: str = ""
    requested_at: str = field(default_factory=_utc_now)
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.actor_id or not isinstance(self.actor_id, str):
            raise ValueError("actor_id must be a non-empty string")
        if not self.capability_name or not isinstance(self.capability_name, str):
            raise ValueError("capability_name must be a non-empty string")

    @classmethod
    def for_capability(
        cls,
        capability: Capability,
        actor_id: str,
        resource: str = "",
        purpose: str = "",
        metadata: Optional[Dict[str, str]] = None,
    ) -> "CapabilityRequest":
        return cls(
            actor_id=actor_id,
            capability_name=capability.name,
            resource=resource,
            purpose=purpose,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "policy_model_version": POLICY_MODEL_VERSION,
            "actor_id": self.actor_id,
            "capability_name": self.capability_name,
            "resource": self.resource,
            "purpose": self.purpose,
            "requested_at": self.requested_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityRequest":
        return cls(
            actor_id=d["actor_id"],
            capability_name=d["capability_name"],
            resource=d.get("resource", ""),
            purpose=d.get("purpose", ""),
            requested_at=d.get("requested_at") or _utc_now(),
            metadata=dict(d.get("metadata") or {}),
        )


def _check_bound(axis: str, value) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{axis} must be an integer or None, got {value!r}")
    if value < 0:
        raise ValueError(f"{axis} must be >= 0 or None, got {value}")


@dataclass(frozen=True)
class ExecutionBudget:
    """Bounded-execution intent. None means 'no bound on this axis' — but a
    budget with NO bound on ANY axis is invalid (unbounded execution is never
    representable). Zero is a valid bound meaning 'this axis permits nothing'.
    """

    max_iterations: Optional[int] = None
    max_runtime_seconds: Optional[int] = None
    max_items: Optional[int] = None
    max_pages: Optional[int] = None
    max_retries: Optional[int] = None
    max_network_calls: Optional[int] = None

    _AXES = (
        "max_iterations",
        "max_runtime_seconds",
        "max_items",
        "max_pages",
        "max_retries",
        "max_network_calls",
    )

    def __post_init__(self):
        for axis in self._AXES:
            _check_bound(axis, getattr(self, axis))
        if all(getattr(self, axis) is None for axis in self._AXES):
            raise ValueError(
                "ExecutionBudget must bound at least one axis "
                "(fully unbounded budgets violate the bounded-execution principle)"
            )

    def to_dict(self) -> dict:
        d = {"policy_model_version": POLICY_MODEL_VERSION}
        for axis in self._AXES:
            d[axis] = getattr(self, axis)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionBudget":
        kwargs = {axis: d.get(axis) for axis in cls._AXES}
        return cls(**kwargs)
