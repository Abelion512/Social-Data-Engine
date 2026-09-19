#!/usr/bin/env python3
"""
ActorHarness — the minimal, provider-independent Harness / Actor Contract.

Owns (HARNESS boundary — see docs/RUNTIME.md §H):
    - actor lifecycle bookkeeping (CREATED → STARTED → terminal state)
    - input validation (RunInput is fail-closed and serializable)
    - declared-capability validation (STRUCTURAL ONLY — vocabulary/config,
      never evaluated here; evaluation lives in src/policy/evaluator.py)
    - POLICY GATE (v0): when a PolicyProfile is supplied, every declared
      capability is evaluated by the deterministic deny-by-default
      PolicyEvaluator BEFORE the runtime is invoked. DENY / REQUIRE_APPROVAL
      ⇒ the run never executes and leaves NO checkpoint/dataset side effects.
    - actor metadata propagation into run provenance (checkpoint + summary)

Delegates WITHOUT modification to:
    - RUNTIME  (AcquisitionRuntime): budgets, retry, checkpoint, persistence,
      termination, metrics
    - PROVIDER/ACTOR code: fetching, parsing, session behavior

This module adds NO new abstraction layer around the runtime: `run()` ends by
returning the runtime's own `RunSummary`, enriched with actor provenance.
A second provider needs nothing here — only an `AcquisitionActor` subclass.

Non-goals (explicitly out of scope): sandboxing, process isolation,
scheduling, autonomous loops, secret transport, approval workflows
(REQUIRE_APPROVAL suspends the run; no approval store exists yet).
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from src.policy.models import (
    POLICY_MODEL_VERSION,
    Capability,
    CapabilityRequest,
    PolicyDecision,
    is_valid_identifier,
)
from src.runtime.context import require_slug_identifier
from src.policy.evaluator import PolicyDecisionRecord, PolicyEvaluator, PolicyProfile
from src.runtime.context import RunContext
from src.runtime.engine import AcquisitionRuntime, RunOptions, RunSummary
from src.runtime.actor import AcquisitionActor
from src.runtime.termination import classify_termination, lifecycle_for_outcome

__all__ = ["RunInput", "ActorHarness", "SECRET_SCAN_PATTERNS",
           "scan_contract_for_secrets"]

# ── Secret-scanner stub (S-G8 stub per threat model §3.D item 5 / FR-SEC-004)
#
# DETERMINISTIC pattern pass over ``RunInput.payload``/``config`` — no LLM,
# no network, no heuristics beyond fixed regexes. This is a STUB: it catches
# the defined secret shapes loudly at contract construction and makes NO
# completeness claim. The full mechanical scanner stays scheduled S-G8.
SECRET_SCAN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    # key=value style assignment to a secret-like name ("password=hunter2")
    ("secret_like_assignment", re.compile(
        r"(?i)\b(password|passwd|api[_-]?key|apikey|access[_-]?token|"
        r"auth[_-]?token|session[_-]?id|sessionid|credential|client[_-]?secret)"
        r"s?\b\s*[:=]\s*\S")),
    ("bearer_header", re.compile(r"(?i)\bbearer\s+[a-z0-9._-]{10,}")),
    ("jwt_shape", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
)

# Dict KEYS whose very NAME marks them as secret carriers: any non-empty
# string content under such a key is refused regardless of its shape.
_SECRET_KEY_NAME = re.compile(
    r"(?i)^\s*(x[-_])?(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|"
    r"secret|client[_-]?secret|password|passwd|pwd|credentials?|"
    r"session[_-]?id|sessionid|bearer|token)s?\s*$")


def _carries_string_content(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(_carries_string_content(v) for v in value)
    return False


def scan_contract_for_secrets(value, field_name: str, _path: str = "") -> None:
    """Recursively pattern-scan a contract value; raise on secret-like content.

    Fails LOUDLY (ValueError naming the field and path) BEFORE any run can
    carry the value into checkpoints, datasets or logs. Scans dict KEYS and
    string leaves; recurses through dicts/lists. Deterministic by design.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            # Secret-named keys carrying ANY string content are refused:
            # shape analysis cannot keep up with token formats, names can.
            if (isinstance(k, str) and _SECRET_KEY_NAME.match(k)
                    and _carries_string_content(v)):
                raise ValueError(
                    f"{field_name}: secret-like key {k!r} at {_path or '(root)'} "
                    "carries content — contracts are NOT a secret transport "
                    "(FR-SEC-004 stub; asset rule §0.1: Critical assets never "
                    "enter payloads)"
                )
            scan_contract_for_secrets(k, field_name, f"{_path}.key({k!r:.64})")
            child_path = f"{_path}.{k}" if isinstance(k, str) else _path
            scan_contract_for_secrets(v, field_name, child_path)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            scan_contract_for_secrets(v, field_name, f"{_path}[{i}]")
    elif isinstance(value, str):
        for pattern_name, rx in SECRET_SCAN_PATTERNS:
            if rx.search(value):
                raise ValueError(
                    f"{field_name}: secret-like content detected "
                    f"(pattern '{pattern_name}') at {_path or '(root)'} — "
                    "contracts are NOT a secret transport (FR-SEC-004 stub; "
                    "asset rule §0.1: Critical assets never enter payloads)"
                )

# Keys the harness itself reserves inside RunContext.target provenance.
_RESERVED_TARGET_KEYS = frozenset({
    "url", "actor_id", "actor_version", "capabilities", "policy_model_version",
    "policy_version",
})

_IDENTIFIER_DOC = "lowercase dot-namespaced identifier (e.g. 'acquisition.tiktok')"

# S-G1 part 1 (threat model §3.D item 3): the DEFAULT profile installed when
# no policy is supplied. Empty rules ⇒ every declared capability DENIES
# ("no_matching_rule"), zero-capability actors DENY via the explicit
# no-declaration branch — the harness refuses execution unless the operator
# either supplies an explicit policy or flips the audited trusted_operator
# switch (Phase 2 window only; removed at Phase 3 CLI unification).
_DENY_ALL_PROFILE = PolicyProfile(name="deny-all-default", version="0", rules=())

# Int budget axes subject to PolicyProfile ceiling clamps (S-G4 / §3.D item 4).
_CLAMPABLE_AXES = (
    "max_items", "max_pages", "max_retries",
    "max_empty_retries", "max_stalls", "max_parse_retries",
)


def _valid_version(value) -> bool:
    """Structural version check: non-empty printable string without whitespace."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 64
        and value.strip() == value
        and len(value.split()) == 1
        and all(ch.isprintable() for ch in value)
    )


@dataclass(frozen=True)
class RunInput:
    """Validated, serializable input for one actor run.

    Represents exactly what the request asks a run to carry — no schema
    framework:
      - target/input payload : ``target_url`` + free-form ``payload`` metadata
      - optional configuration: ``config`` (keys must be ``RunOptions`` axes
        when run through the harness; anything else fails loudly)
      - actor identity/version: ``actor_id`` / ``actor_version`` — the caller's
        statement of WHAT is being run; the harness verifies it matches the
        actor object before execution (fail-closed identity binding)

    NON-SECRET by contract AND by enforcement: ``payload``/``config`` are
    provenance/policy context only and are pattern-scanned for secret-like
    values at construction (``scan_contract_for_secrets``, FR-SEC-004 stub).
    Identity fields follow the S-G2 slug charset: ``job_id``/``run_id`` must
    be lowercase slug identifiers — traversal or absolute-path identifiers
    fail closed here, before any context or filesystem object can exist.
    """
    actor_id: str
    actor_version: str
    provider: str
    target_url: str
    payload: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    run_id: Optional[str] = None

    def __post_init__(self):
        for axis, label in (
            (self.actor_id, "actor_id"),
            (self.provider, "provider"),
        ):
            if not is_valid_identifier(axis):
                raise ValueError(f"{label} must be a {_IDENTIFIER_DOC}, got {axis!r}")
        if not _valid_version(self.actor_version):
            raise ValueError(
                f"actor_version must be a short non-empty version string "
                f"without whitespace, got {self.actor_version!r}"
            )
        if not isinstance(self.target_url, str) or not self.target_url.strip():
            raise ValueError("target_url must be a non-empty string")
        for name, value in (("payload", self.payload), ("config", self.config)):
            if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
                raise ValueError(f"{name} must be a dict with string keys")
        for name in ("job_id", "run_id"):
            v = getattr(self, name)
            if v is None:
                continue
            # S-G2 item 1: strict slug charset, error names the field.
            require_slug_identifier(v, name)
        # S-G8 stub: deterministic secret scan over payload/config values.
        for name in ("payload", "config"):
            scan_contract_for_secrets(getattr(self, name), name)

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "actor_version": self.actor_version,
            "provider": self.provider,
            "target_url": self.target_url,
            "payload": dict(self.payload),
            "config": dict(self.config),
            "job_id": self.job_id,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RunInput":
        if not isinstance(d, dict):
            raise ValueError("RunInput.from_dict expects a dict")
        required = ("actor_id", "actor_version", "provider", "target_url")
        missing = [k for k in required if k not in d]
        if missing:
            raise ValueError(f"RunInput missing required keys: {missing}")
        known = set(required) | {"payload", "config", "job_id", "run_id"}
        unknown = sorted(set(d) - known)
        if unknown:
            raise ValueError(f"RunInput has unknown keys: {unknown}")
        return cls(
            actor_id=d["actor_id"],
            actor_version=d["actor_version"],
            provider=d["provider"],
            target_url=d["target_url"],
            payload=dict(d.get("payload") or {}),
            config=dict(d.get("config") or {}),
            job_id=d.get("job_id"),
            run_id=d.get("run_id"),
        )


class ActorHarness:
    """Validates an actor declaration, binds identity, then delegates to the
    runtime. Deliberately thin: it wraps NOTHING at execution time."""

    def __init__(self, runtime: Optional[AcquisitionRuntime] = None,
                 print_fn=print,
                 state_dir: str = "state/runs",
                 data_dir: str = "data/runs",
                 policy: Optional[PolicyProfile] = None,
                 *, trusted_operator: bool = False):
        self._runtime = runtime or AcquisitionRuntime(print_fn=print_fn)
        self._print = print_fn
        self.state_dir = state_dir
        self.data_dir = data_dir
        # POLICY GATE DEFAULT (S-G1 part 1 / §3.D item 3): with no explicit
        # policy the harness installs the empty DENY-ALL profile instead of
        # silently trusting the caller. Trusted-operator mode survives ONLY
        # as this explicit keyword-only switch during the Phase 2 window
        # (removed at Phase 3 CLI unification). Passing BOTH a policy and the
        # escape hatch is ambiguous intent and fails closed here.
        if trusted_operator and policy is not None:
            raise ValueError(
                "ambiguous harness configuration: supply EITHER an explicit "
                "policy profile OR trusted_operator=True, not both"
            )
        if policy is not None:
            self._policy_profile = policy          # enforced mode
        elif trusted_operator:
            self._policy_profile = None            # documented operator opt-out
        else:
            self._policy_profile = _DENY_ALL_PROFILE   # deny-by-default

    # ── public entry point ────────────────────────────────────────────────
    async def run(self, actor: AcquisitionActor, run_input: RunInput,
                  options: Optional[RunOptions] = None) -> RunSummary:
        """Validate + bind, build the RunContext, execute through the runtime.

        Lifecycle: the run enters CREATED here (input validated, context not
        yet built); the runtime persists STARTED in checkpoints while running
        and stamps the terminal lifecycle state (COMPLETED / FAILED /
        TERMINATED) into the returned RunSummary.
        """
        if not isinstance(run_input, RunInput):
            raise ValueError("run_input must be a RunInput instance")
        self._require_contract_actor(actor)
        self._bind_identity(actor, run_input)
        capabilities = self.validate_declared_capabilities(actor)
        opts = self._resolve_options(run_input, options)

        payload_overlap = _RESERVED_TARGET_KEYS & set(run_input.payload)
        if payload_overlap:
            raise ValueError(
                f"payload keys collide with harness-reserved provenance keys: "
                f"{sorted(payload_overlap)}"
            )

        target_meta: Dict[str, Any] = {
            "actor_id": actor.actor_id,
            "actor_version": actor.actor_version,
            "capabilities": [c.name for c in capabilities],
            "policy_model_version": POLICY_MODEL_VERSION,
        }
        if self._policy_profile is not None:
            target_meta["policy_version"] = self._policy_profile.version
        target_meta.update(run_input.payload)

        ctx = RunContext.create(
            provider=run_input.provider,
            target_url=run_input.target_url,
            job_id=run_input.job_id,
            run_id=run_input.run_id,
            state_dir=self.state_dir,
            data_dir=self.data_dir,
            target_meta=target_meta,
        )

        # POLICY GATE — enforcement point per SDD §2 ownership matrix
        # ("Gating: Harness"). Runs AFTER context construction but BEFORE the
        # runtime is invoked — strictly earlier than the architecture-doc
        # "insertion point 2" sketch (engine loop, pre-fetch_page): a non-ALLOW
        # decision therefore returns a summary WITHOUT invoking the runtime and
        # WITHOUT writing any checkpoint or dataset bytes.
        if self._policy_profile is not None:
            decision = self._evaluate_run_policy(actor, capabilities, run_input, ctx)
            if decision.decision == PolicyDecision.DENY:
                self._print(f"[harness] POLICY DENIED {ctx.job_id}: "
                            f"{decision.capability} ({decision.reason})")
                return self._gated_summary(ctx, actor, decision, "policy_denied")
            if decision.decision == PolicyDecision.REQUIRE_APPROVAL:
                self._print(f"[harness] APPROVAL REQUIRED {ctx.job_id}: "
                            f"{decision.capability} ({decision.reason})")
                return self._gated_summary(ctx, actor, decision, "approval_required")
            self._print(f"[harness] policy ALLOW {ctx.job_id}: "
                        f"profile {self._policy_profile.name} "
                        f"v{self._policy_profile.version}")

        # Execution, persistence, budgets, termination: RUNTIME-owned, called
        # unmodified. The harness holds no state between runs (stateless gate).
        return await self._runtime.run(actor, ctx, opts)

    # ── validation (fail-closed, structural ONLY) ─────────────────────────
    def _require_contract_actor(self, actor) -> None:
        if not isinstance(actor, AcquisitionActor):
            raise ValueError(
                "harness requires an AcquisitionActor subclass — got "
                f"{type(actor).__name__}"
            )
        if not is_valid_identifier(actor.actor_id):
            raise ValueError(
                f"actor {type(actor).__name__} declares invalid or missing "
                f"actor_id ({actor.actor_id!r}); expected {_IDENTIFIER_DOC}"
            )
        if not _valid_version(actor.actor_version):
            raise ValueError(
                f"actor {type(actor).__name__} declares invalid or missing "
                f"actor_version ({actor.actor_version!r})"
            )

    def _bind_identity(self, actor: AcquisitionActor, run_input: RunInput) -> None:
        if run_input.actor_id != actor.actor_id:
            raise ValueError(
                f"identity mismatch: RunInput targets actor_id="
                f"{run_input.actor_id!r} but the actor declares "
                f"{actor.actor_id!r}"
            )
        if run_input.actor_version != actor.actor_version:
            raise ValueError(
                f"identity mismatch: RunInput targets actor_version="
                f"{run_input.actor_version!r} but the actor declares "
                f"{actor.actor_version!r}"
            )

    @staticmethod
    def validate_declared_capabilities(actor: AcquisitionActor) -> Tuple[Capability, ...]:
        """Structural validation of an actor's capability DECLARATION.

        - zero capabilities is valid (nothing consumes declarations yet)
        - every entry must be a policy-model ``Capability``
        - duplicates are rejected (a duplicated declaration is an author bug)
        - names are NOT checked against a fixed vocabulary: the vocabulary is
          intentionally extensible (src/policy/models.py); membership checks
          belong to the future policy evaluator, not to declaration

        No ALLOW/DENY evaluation happens anywhere in this method.
        """
        declared = actor.capabilities()
        if declared is None:
            raise ValueError(
                f"actor {type(actor).__name__}.capabilities() returned None; "
                "return a tuple (empty tuple for 'no capabilities')"
            )
        seen: set = set()
        for cap in declared:
            if not isinstance(cap, Capability):
                raise ValueError(
                    f"actor {type(actor).__name__} declared a non-Capability: "
                    f"{cap!r}; declare src.policy.models.Capability instances"
                )
            if cap.name in seen:
                raise ValueError(
                    f"actor {type(actor).__name__} declared duplicate "
                    f"capability {cap.name!r}"
                )
            seen.add(cap.name)
        return tuple(declared)

    def _evaluate_run_policy(
        self,
        actor: AcquisitionActor,
        capabilities: Tuple[Capability, ...],
        run_input: RunInput,
        ctx: RunContext,
    ) -> PolicyDecisionRecord:
        """Evaluate EVERY declared capability against the enforced profile.

        Semantics (deterministic):
        - zero capabilities under an enforced policy ⇒ DENY: a run that
          declares nothing still performs network/browser work implicitly;
          under enforcement nothing is granted by default (fail closed).
        - first non-ALLOW verdict (in declaration order) wins and is returned.
        - all declared capabilities allowed ⇒ the last ALLOW record is
          returned for audit.

        Declaring EXTRA capabilities can therefore never BYPASS the gate:
        each additional declared capability must ALSO be explicitly allowed,
        otherwise the run is denied.
        """
        evaluator = PolicyEvaluator(self._policy_profile)
        if not capabilities:
            return PolicyDecisionRecord(
                actor_id=getattr(actor, "actor_id", ""),
                capability="(none-declared)",
                resource=run_input.target_url,
                decision=PolicyDecision.DENY,
                policy_version=self._policy_profile.version,
                reason="no_capabilities_declared_under_enforced_policy",
            )
        first_blocker: Optional[PolicyDecisionRecord] = None
        allow_record: Optional[PolicyDecisionRecord] = None
        for cap in capabilities:
            request = CapabilityRequest.for_capability(
                cap,
                actor_id=actor.actor_id,
                resource=run_input.target_url,
                purpose="acquisition-run",
                metadata={},  # metadata is NEVER trusted for authorization
            )
            record = evaluator.evaluate(request, provider=run_input.provider)
            if (
                record.decision != PolicyDecision.ALLOW
                and first_blocker is None
            ):
                first_blocker = record
            allow_record = record
        return first_blocker or allow_record  # type: ignore[return-value]

    def _gated_summary(
        self,
        ctx: RunContext,
        actor: AcquisitionActor,
        decision: PolicyDecisionRecord,
        termination_reason: str,
    ) -> RunSummary:
        """Terminal summary for a policy-gated (non-executed) run.

        Side-effect-free by construction: no checkpoint commit, no dataset
        write, no pagination state — only this summary carries the audited
        decision (full record in metrics['policy_decision']).
        """
        outcome = classify_termination(termination_reason)
        return RunSummary(
            run_id=ctx.run_id,
            job_id=ctx.job_id,
            provider=ctx.provider,
            resumed=False,
            termination_reason=termination_reason,
            outcome=outcome,
            items_written=0,
            items_seen=0,
            metrics={"policy_decision": decision.to_dict()},
            dataset_path=str(ctx.dataset_path),
            checkpoint_path=str(ctx.checkpoint_path),
            actor_id=getattr(actor, "actor_id", ""),
            actor_version=getattr(actor, "actor_version", ""),
            lifecycle_state=lifecycle_for_outcome(outcome),
        )

    def _resolve_options(self, run_input: RunInput,
                         options: Optional[RunOptions]) -> RunOptions:
        """Configuration precedence: explicit RunOptions wins; otherwise the
        RunInput.config axes are projected onto RunOptions. Supplying both is
        rejected (ambiguous intent fails closed instead of silently merging)."""
        if options is not None:
            if run_input.config:
                raise ValueError(
                    "ambiguous run configuration: pass budget axes either via "
                    "RunInput.config or via RunOptions, not both"
                )
            return self._apply_ceiling_clamps(options)
        if not run_input.config:
            return self._apply_ceiling_clamps(RunOptions())

        option_fields = {f: getattr(RunOptions(), f) for f in vars(RunOptions())}
        unknown = sorted(set(run_input.config) - set(option_fields))
        if unknown:
            raise ValueError(
                f"config contains non-RunOptions axes: {unknown}; supported: "
                f"{sorted(option_fields)}"
            )
        for key, value in run_input.config.items():
            expected = type(option_fields[key])
            if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"config.{key} must be an integer, got {value!r}")
            if expected is bool and not isinstance(value, bool):
                raise ValueError(f"config.{key} must be a boolean, got {value!r}")
        return self._apply_ceiling_clamps(RunOptions(**dict(run_input.config)))

    def _apply_ceiling_clamps(self, options: RunOptions) -> RunOptions:
        """S-G4 ceiling clamps (§3.D item 4): the caller may LOWER a budget
        axis below its ceiling but may NEVER exceed it. Applies to explicitly
        passed RunOptions AND config-projected options alike; every clamp is
        printed for audit. Clamping happens BEFORE execution — never after."""
        profile = self._policy_profile
        if profile is None:
            return options
        clamped: Dict[str, int] = {}
        updates: Dict[str, int] = {}
        for axis in _CLAMPABLE_AXES:
            ceiling = getattr(profile, axis, None)
            if ceiling is None:
                continue
            current = getattr(options, axis)
            if current > ceiling:
                updates[axis] = ceiling
                clamped[axis] = current
        if updates:
            self._print(
                f"[harness] budget clamp ({profile.name} v{profile.version}): "
                + ", ".join(f"{a} {clamped[a]}→{updates[a]}" for a in updates)
            )
            return dataclasses.replace(options, **updates)
        return options
