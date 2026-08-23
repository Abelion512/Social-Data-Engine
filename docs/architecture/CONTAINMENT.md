# Containment Architecture — mechanisms per maturity level

Companion to `docs/SECURITY-THREAT-MODEL.md` (threats, severities, S-gates) and
`DECISIONS.md` D-014. This document defines **what containment means
mechanically** at each ladder level and what must be provably true before a
promotion. It deliberately repeats the rule from the threat model: controls
must live *outside the model's reasoning* — in constructors, path resolution,
the evaluator, the OS — never in prompts or conventions.

Status vocabulary: `enforced` (code+tests), `partial`, `documented`,
`planned`.

---

## 1. The ladder (recap)

```
L0  trusted operator            ← TODAY (operating)
L1  policy-gated actor          ← components exist; not yet mandatory
L2  capability isolation
L3  filesystem/network containment
L4  process/sandbox isolation   ⚠ reverses PRODUCT.md §8 non-goal #6;
                                   requires decision amendment first
L5  autonomous multi-agent execution
```

Promotion is one-directional per subsystem and requires: (a) the level's gates
green (`ROADMAP.md` §Security gates), (b) a promotion note citing those tests
in `DECISIONS.md`-style record, (c) no known open P0 against the entering
level's surface.

**Upgrade law (binding — threat model §1.1, `DECISIONS.md` D-015).** Each
transition requires its enforcement class: L0→L1 mandatory gating + safe ids
(S-G1/G2); L1→L2 capability enforcement (S-G3/G4/G5); L2→L3 the
filesystem/network boundary (S-G6/G7/G8/G10); L3→L4 process isolation
(PRODUCT.md §8 amendment first, then S-G9); L4→L5 multi-agent isolation +
tamper-evident audit (S-G11–G14). **Implementing or scheduling level N+1
mechanisms while level N's gates are not green is a merge-blocking
violation** — describing a level, even in these docs, is not permission to
build it. Assets and their allowed access are classified in threat model
§0.1; trust zones and crossing rules in §0.2.

---

## 2. L0 — Trusted operator (today)

**Meaning:** everything runs with operator privileges. The harness gate is
optional; CLI predates it; filesystem/network boundaries are conventions.
Legitimate because the operator personally launches every process.

**What is already enforced here (keep forever):**

| Control | Where | Status |
|---|---|---|
| Budgets bound ≥1 axis; fully-unbounded unrepresentable | `src/policy/models.py::ExecutionBudget` | enforced |
| HARD unique-item cap incl. mid-page truncation | `src/runtime/engine.py` | enforced |
| Retry/stall/empty/parse budgets → classified termination | `engine.py` + `state.py` + `termination.py` | enforced |
| Corrupt checkpoint ⇒ loud terminal failure, never silent fresh-run | `checkpoint.py`, `engine.py` | enforced |
| Unknown reasons/outcomes fail closed (PERMANENT_FAILURE / FAILED) | `termination.py`, `loop_state.py` | enforced |
| Loop budget cap (`MAX_ITER_CAP=10`), durable position, idempotent iterations, terminal-state honor | `improve.py`, `loop_state.py` | enforced |
| Deterministic deny-by-default evaluator; exceptions ⇒ DENY; metadata never authorizes | `src/policy/evaluator.py` | enforced |
| Zero plaintext credentials in code (grep gate); cookie-based auth only | CI checklist | enforced (process) |
| Fail-closed input contracts (`RunInput`, config-axis projection, reserved-key collisions) | `runtime/harness.py` | enforced |

**Known containment holes accepted at L0:** optional gate, ungated CLI, id-path
interpolation, unrestricted egress, host-trust actors. Each is a tracked threat
(TM-xx) with an owning gate — acceptance ends when exposure widens.

---

## 3. L1 — Policy-gated actor

**Actor's powers:** submit serialized `RunInput`; nothing executes except
through the harness gate; denials are loud, classified, side-effect-free.

**Mechanisms that must be true (all outside the actor):**

1. **Mandatory gate default (S-G1).** Constructing the harness without a
   profile installs the empty deny-everything profile. Escaping to trusted mode
   is an explicit named switch, visible in code review, removed when the CLI
   unifies. *Planned (PR #9).*
2. **Entry-point monopoly (S-G1).** Every sanctioned entry point routes through
   the gate: import API now, CLI at Phase 3 unification, any future HTTP/job
   surface never bypasses. Mechanical check: import audit asserting no runtime/
   engine path reachable from entry modules skips `ActorHarness.run`.
3. **Identity-safe identifiers (S-G2).** `job_id`, `run_id`, `video_id`,
   `actor_id`, `provider` accept slug-charsets only; path builders assert the
   resolved file lives under its declared root (`commonpath` check) before any
   write. Corrupt/hostile ids raise naming the field. *Planned (PR #9).*
4. **Denial surfacing.** `policy_denied` / `approval_required` summaries carry
   the full `PolicyDecisionRecord`; legacy reasons unchanged (additive).
   *Enforced for the gated path today.*

**Promotion test shape:** adversarial-input table (traversal ids, unknown axes,
oversized values, malformed payloads) × every entry point ⇒ either clean
validation error or `policy_denied` summary, and **zero new files on disk**.

---

## 4. L2 — Capability isolation

**Actor's powers:** perform actions that map to granted capabilities, within
profile-clamped budgets, inside its own state namespace.

**Mechanisms:**

1. **Action↔capability linkage (S-G3).** The engine evaluates before each
   page-fetch (POLICY-ARCHITECTURE insertion point 2) and emits audit events at
   dataset append (point 4). Provider tools declare capabilities matching their
   behavior (`browser.read` ⇒ `browser.automate`, etc.), so the experimental
   toolkit cannot act un-declared once wired as a page source. Budget checks
   happen BEFORE dispatch (counterexample #2 discipline).
2. **Ceiling clamps (S-G4).** Profiles carry maximum budgets; `_resolve_options`
   clamps caller-supplied values down, never up. A submitted intent can be less
   powerful than requested, never more. Wall-clock/network axes become real
   consumed limits (with S-G10).
3. **State namespaces + binding (S-G5).** Checkpoints store
   `(actor_id, actor_version)` and resume refuses mismatches; per-actor
   subdirectories under `state/`+`data/` remove cross-actor collision classes
   (TM-07); a single-writer lock (advisory lockfile) makes concurrent attach
   refuse loudly instead of interleaving (TM-08).
4. **Registry hygiene (TM-22).** Builtin provider routes freeze; adapter
   registration becomes a privileged operation unavailable to submitted runs;
   import audits extended to `src/harness/registry.py` consumers.

**Promotion test shape:** a capability-denying profile makes an actor that
"does extra things" observable (denied action + audit event), and clamp tests
prove ceilings bind regardless of caller config.

---

## 5. L3 — Filesystem/network containment

**Mechanisms:**

1. **Workspace jail (S-G6).** All writers (datasets, checkpoints, manifests,
   loop state, exports, traces) resolve paths through one jail helper:
   absolute-resolve → verify under declared root → reject symlink escape
   (resolve parent chain) → open. One helper, everywhere; direct `Path` use in
   writers fails review.
2. **Egress allowlist (S-G7).** Default-deny network policy at the environment
   layer (proxy/firewall/profile), with an explicit allowlist of platform API
   hosts + approved endpoints. The vision-captcha third-party call runs only
   behind REQUIRE_APPROVAL (D-007) and its destination joins the allowlist
   explicitly. Route intercepts scope patterns to platform hosts, never `**/*`
   in production paths.
3. **Credential isolation.** Dedicated automation browser profile (never the
   operator's daily profile); cookie stores 0600 under `~/.tiktok-linkedin/`;
   repo-relative cookie probing removed; log/event redaction at boundaries;
   mechanical secret scanner over contract payloads (S-G8) fails builds loudly.

**Promotion test shape:** jail unit tests (traversal/symlink/absolute escapes);
egress integration test proving non-allowlisted connect attempts fail from a
run context; scanner trip-wire tests.

---

## 6. L4 — Process/sandbox isolation ⚠

**Reversal requirement first.** PRODUCT.md §8 lists sandboxing as OUT by
decision (FR-SEC-001 documented limitation). Entering L4 therefore starts with
a decision-record amendment superseding that non-goal — roadmap drift alone is
forbidden by SDD §7 evolution rules.

**Mechanisms once adopted:**
- Actors execute out-of-process (subprocess/container/jail-class boundary);
  capability grants pass in as serialized manifests; results return via the
  same `RunInput`/`RunSummary` discipline (no shared memory state).
- Kill-switch verb honored between actions (Constitution §10 override verbs
  become real: stop/approve/deny/escalate wired into the loop with the approval
  store).
- Host secrets (env keys, cookie stores) absent from actor processes by
  construction; brokered access returns opaque handles.

**Promotion test shape:** IPC contract tests; kill-switch latency/betweenness
tests; secret-absence proof inside actor processes.

---

## 7. L5 — Autonomous multi-agent execution

**Mechanisms:** derived attenuating grants (child ⊆ parent ∩ profile — S-G11),
aggregate budget axis across loops/agents (S-G12), independent state namespaces
per agent, tamper-evident audit chain attributing every action to an agent
identity (S-G13), lesson-integrity gates before any cross-run learning consumes
captured data (S-G14, TM-16). Strategic autonomy still external (D-001 holds at
every level).

---

## 8. Standing rules (all levels)

1. Fail closed on uncertainty — evaluator errors, corrupt state, unknown
   reasons classify to denial/failure, never to allow.
2. Evidence before claim; evidence itself gets integrity protection by S-G13;
   until then, absence of integrity is stated wherever evidence is presented.
3. Additive evolution of reasons/states/fields — security fixes never rename
   byte-compatible checkpoint strings.
4. Human decisions stay human: destructive recovery, approvals, login/captcha,
   and any level promotion are recorded human choices, never automated.
5. Docs may not outrun tests (NFR-010) — including these ones: each "planned"
   above moves to "enforced" only with its gate's tests merged.
