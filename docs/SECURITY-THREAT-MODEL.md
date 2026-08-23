# Security Threat Model v1 — Social Data Engine

PR #8 deliverable · 2026-08-23 · Status: **binding reference** for all autonomy
increases. Companion docs: `docs/architecture/CONTAINMENT.md` (mechanisms per
maturity level), `DECISIONS.md` **D-014** (the ladder binds autonomy),
`ROADMAP.md` §Security gates (S-gates).

---

## 0. Scope & ground rules

**Asset under attack:** the bounded acquisition pipeline (declared intent →
budgeted run → dataset + provenance) and its evidence chain (checkpoints,
summaries, manifests), plus the host environment it runs in.

**Adversary assumption.** The *model / actor* is potentially adversarial and MAY:
ignore instructions, request excessive capabilities, attempt filesystem escape,
access secrets, exfiltrate data, abuse network access, execute unintended
processes, modify its own policy/configuration, bypass the harness, loop within
allowed iterations, corrupt checkpoints, exploit another actor's state, abuse
parallel execution, write outside its workspace, or manipulate audit/provenance
records. Per the PR brief: **no control may assume an LLM obeys policy** — every
control below is judged by whether it is enforced *outside the model's reasoning*.

**Evidence basis.** This model audits code as of this branch (`feat` line
including merged PR #6 PolicyEvaluator v0 and PR #7 LoopState). Note: several
docs lag the code — `CURRENT-STATE.md` still lists the evaluator as NOT
IMPLEMENTED while `src/policy/evaluator.py` + the harness gate exist and
`tests/test_policy_evaluator.py` exists. Where doc and code disagree, the
**code** is audited here; the drift itself is tracked as a finding (TM-16).

**Severity scale**

| Level | Meaning |
|---|---|
| P0 | Exploitable through a sanctioned entry point today, or blocks any autonomy increase |
| P1 | Real bypass requiring specific but plausible conditions; must close before exposure grows |
| P2 | Abuse/degradation/integrity loss; bounded blast radius today |
| P3 | Hygiene / hardening |

**Control status vocabulary** (mirrors the Constitution): `enforced`,
`documented`, `planned`.

---

## 1. Security maturity ladder

Autonomy is earned, never assumed. Each level defines what MUST be true before
promotion to the next. Promotion requires the cited S-gates green (see
`ROADMAP.md` §Security gates) plus a decision-record note per promotion.
Detailed mechanisms per level live in `CONTAINMENT.md`.

| Level | Name | Actor's effective powers | MUST be true before promoting INTO this level |
|---|---|---|---|
| **L0** | Trusted operator | Everything runs with operator privileges; policy gate optional | — (baseline; today's default mode) |
| **L1** | Policy-gated actor | Submits serialized `RunInput`; execution only via harness whose evaluator gate is ON; deny-by-default | S-G1 (mandatory gate on sanctioned entries incl. CLI), S-G2a (identity-safe job/video ids), `policy_denied` surfaced + tested |
| **L2** | Capability isolation | Actions are intercepted and matched to granted capabilities; budgets clamped by profile ceilings; state namespaced per actor | S-G3 (declaration↔action linkage at runtime insertion points 2+4), S-G4 (profile ceiling clamps RunOptions), S-G5 (per-actor state namespaces; checkpoint↔actor binding) |
| **L3** | Filesystem/network containment | Writes confined to declared workspace roots; egress restricted to allowlisted endpoints/proxy; secrets isolated from run processes | S-G6 (workspace jail on all writers), S-G7 (egress allowlist; cookie-store isolation; log secret-redaction), S-G8 (mechanical secret scanner over contracts) |
| **L4** | Process/sandbox isolation | Actors run out-of-process with OS-enforced containment (container/jail/seccomp-class); capability handoff only via serialized contracts; external kill switch honored between actions | S-G9 (actor subprocess boundary + IPC contract tests), S-G10 (kill switch + wall-clock/network budget axes consumed). ⚠ Reverses PRODUCT.md §8 non-goal #6 — requires explicit decision amendment first |
| **L5** | Autonomous multi-agent execution | Sub-agents/worktrees spawned with derived, strictly-attenuating grants; aggregate budgets across agents; tamper-evident audit chain | S-G11 (attenuation proof: child grant ⊆ parent grant ∩ profile), S-G12 (aggregate budget axis), S-G13 (signed/hash-chained manifests + approval store live) |

**Current position: L0 operating, L1 components present.** The evaluator v0 and
harness gate exist (PR #6/#7 lineage), but the gate is opt-in, the legacy CLI
runs ungated, and filesystem/network controls are conventions. SDE is **not**
yet L1 by its own ladder.

---

## 2. Component-by-component audit

Each subsection states the component's role, then the threat rows. Format per
threat: **attack surface → boundary → enforcement → bypass path → blast radius
→ mitigation → status → severity**.

### 2.1 ActorHarness (`src/runtime/harness.py`)

Role: sanctioned front door. Validates identity binding (`_bind_identity`),
structurally validates declarations, projects config onto `RunOptions` axes
(unknown axes rejected, both-sources rejected), evaluates every declared
capability when a `PolicyProfile` is supplied, returns side-effect-free
`policy_denied` / `approval_required` summaries without invoking the runtime.

Strengths verified in code: identity binding fail-closed before context build;
reserved-key collision check on `payload`; zero-capabilities-under-enforced-
profile ⇒ DENY; declaring extra capabilities cannot bypass (each must be
individually allowed); metadata never used for authorization; gated runs write
no checkpoint/dataset bytes.

| ID | Threat | Finding |
|---|---|---|
| TM-01 | **Caller-controlled `job_id` escapes the workspace** | Surface: `RunInput.job_id` (validated only as non-empty string). Boundary: intended = `state/runs/<job>.json`. Enforcement: none — `RunContext.create` interpolates `job_id` directly into paths; `CheckpointStore.save` does `mkdir(parents=True)` and atomic replace; `JsonlDataset.append` creates parent dirs. Bypass: `job_id="../../../../tmp/x"` (or worse) writes attacker-shaped JSON outside the workspace through the *sanctioned* entry point. Blast radius: arbitrary-path file creation/truncation-as-JSON anywhere the process can write; combined with resume semantics, a crafted checkpoint can steer later runs. Mitigation: restrict job ids to a strict slug charset (reuse `is_valid_identifier` discipline) AND confine resolved paths under declared roots with a `commonpath` check, failing closed. Status: mitigation planned (S-G2). Severity: **P0** — this is the flagship filesystem-escape finding; the product's primary users are machine callers that submit `RunInput`s. Same defect class applies to `SelfHealingPipeline.run(video_id=…)` → `LoopStateStore(state_dir/f"{video_id}.json")` and improve manifest paths. |
| TM-02 | **Policy gate is opt-in; legacy CLI is ungated** | Surface: `ActorHarness(policy=None)` default = trusted-operator mode (documented D-006); `src/tiktok_linkedin.py` CLI predates the harness entirely (two entry truths, FR-INT-001 GAP). Enforcement: only when caller supplies a profile. Bypass: don't pass a profile; or use the CLI path. Blast radius: full unbounded acquisition behavior. Mitigation: default-construct a deny-all profile for externally-submitted runs; keep an explicit named trusted-operator switch during the Phase 2 window; unify CLI onto the harness path (Phase 3). Status: documented now, enforcement planned (S-G1). Severity: **P1** (P0 the moment external agents are invited to submit runs). |
| TM-03 | **Declarations are not actions** | The gate evaluates *declared* capabilities once, pre-run. No mechanism maps an actual action (fetch, write, spawn) back to a capability at execution time. Bypass: an in-process actor declares `network.fetch` (allowed), then also writes files, reads env vars, or opens sockets directly — undeclared actions are invisible. Blast radius: full host trust (FR-SEC-001). Mitigation: action-level interception at engine insertion points 2 (pre-fetch) and dataset-append audit events (point 4), evolving toward per-action evaluation (L2/S-G3); until then the limitation must stay surfaced wherever actors are documented. Status: documented (FR-SEC-001); interception planned. Severity: **P1** — structural ceiling of the current design; honesty about it is what keeps the rest credible. |
| TM-04 | **Budget escalation via `RunInput.config`** | Config axes are type-checked but value-unbounded: `max_items=10**9, max_pages=10**9` is a legal config. Combined with unconsumed wall-clock/network axes (FR-ACQ-005 GAP) and the missing aggregate loop budget (SDD §6), one submitted intent can command near-unbounded work inside "budgets". Blast radius: resource exhaustion, ToS exposure, rate-limit burn of the operator's sessions. Mitigation: profile-imposed ceilings that clamp caller budgets (caller can lower, never raise past the profile max); consume time/call axes; declared aggregate budget axis. Status: planned (S-G4 + Phase 3 FR-ACQ-005). Severity: **P1**. |
| TM-05 | **Payload/config content is unvalidated provenance** | Arbitrary JSON values flow into `checkpoint.target` verbatim; size uncapped. Enables forged/misleading provenance text and checkpoint bloat (disk pressure). Mitigation: size caps + typed payload schema + secret scanner over payloads (S-G8). Status: documented non-secret-by-contract only; scanner GAP (FR-SEC-004). Severity: **P3**. |

### 2.2 AcquisitionRuntime (`src/runtime/engine.py`)

Role: provider-blind execution loop; owns budgets (items/pages/retries),
checkpoint/resume, durability ordering (dataset-before-checkpoint),
termination taxonomy, metrics.

Strengths: HARD unique-item cap truncates mid-page; dupes never consume cap;
corrupt checkpoint ⇒ explicit terminal `checkpoint_corrupt`, never silent
fresh-run; unknown reasons classify PERMANENT_FAILURE; failed runs still emit
summaries.

| ID | Threat | Finding |
|---|---|---|
| TM-06 | **Loop forever within allowed iterations** | Item/page/retry/stall budgets terminate the *counting* dimensions, but there is no timeout: a hung `fetch_page` (browser wedged, socket stalled) parks the run indefinitely; wall-clock and network-call budget axes exist in `ExecutionBudget` and are consumed nowhere. Mitigation: consume the axes pre-dispatch (POLICY-ARCHITECTURE counterexample #2: check BEFORE the action), classify new additive reasons. Status: planned (Phase 3, S-G10). Severity: **P2** (bounded pages make true infinity unlikely; hangs still violate "always terminates"). |
| TM-07 | **Cross-actor state hijack** | Checkpoints/datasets key on `job_id` alone; no binding between checkpoint and the actor identity that created it. An adversarial actor pinning another job's `job_id` (with `resume=True`) resumes and appends into another run's dataset and continues its cursor. Harness records actor identity into provenance but resume never verifies it against the stored checkpoint. Mitigation: store `actor_id`/`actor_version` in checkpoints (already partially present via target meta) and verify on resume, fail closed on mismatch (S-G5). Severity: **P2** (single-operator namespace today; becomes real at multi-actor L2). |
| TM-08 | **Parallel execution abuse** | No lock: concurrent invocations on one `job_id` interleave `.tmp` writes and replaces (documented single-writer limitation). Result: lost updates, torn resume semantics, duplicated network work. Mitigation: advisory lockfile/PID guard refusing concurrent attach, or serialize via a run-manager; refusal is fail-closed and loud. Status: documented; enforcement planned (S-G5). Severity: **P2**. |
| TM-09 | **Fresh-run-over-populated-dataset** | Re-invoking a pinned job WITHOUT `resume=True` appends duplicates (RUNTIME.md §G documented limitation). Integrity issue rather than escape; interacts badly with dedup metrics. Mitigation: refuse fresh-run when the derived dataset exists non-empty unless an explicit destructive flag is set (aligns FR-SAF-003 destructive-ops rule). Status: documented; planned. Severity: **P3**. |
| TM-10 | **Substring error laundering** | Error strings merely containing "block"/"verify"/"captcha" classify terminal `auth_blocked` (inherited TikTok heuristic). A hostile page/provider can craft error text to force terminal misclassification (DoS-ish stop) or mask retryable failures. Mitigation: require precise `error_type` codes; treat substring matching as fallback-only. Status: documented in RUNTIME.md §G. Severity: **P3**. |

### 2.3 PolicyEvaluator (`src/policy/evaluator.py`) + models

Role: deterministic deny-by-default decision function over an immutable
versioned profile; no network/LLM; exceptions resolve DENY; metadata never
authorizes; provider identity comes from the trusted call site.

Assessment: this is the strongest link. Fail-closed paths are explicit and
tested (`tests/test_policy_evaluator.py`). Remaining gaps:

| ID | Threat | Finding |
|---|---|---|
| TM-11 | **Profile authoring/tampering surface** | Profiles are constructed in code; no loader exists yet, so there is no file-format attack surface today — but also no immutability guarantee beyond frozen dataclasses. When profiles become loadable configuration (named profiles, FR-POL-005), an adversary able to influence loaded files modifies its own policy. Mitigation: load only from operator-owned read-only locations; version-stamp + hash-pin profiles into run summaries; never accept profiles via `RunInput`/payload. Status: planned constraint; record it NOW so the loader lands safe. Severity: **P2 (prospective)**. |
| TM-12 | **Opaque-resource prefix matching** | Rules match resources as opaque strings (`resource_prefix` startswith). Overly broad prefixes ("https://www.tiktok.com/") grant everything under them including future paths; the evaluator cannot tell. Acceptable for v0 because resources are operator-declared; becomes risky if rules are ever auto-generated. Mitigation: keep rules hand-authored + reviewed; add scheme/host normalization when egress allowlists arrive (L3/S-G7). Severity: **P3**. |

### 2.4 LoopState + SelfHealingPipeline (`src/runtime/loop_state.py`, `src/pipeline/improve.py`)

Role: durable, idempotent, bounded internal repair loops; planner is pure and
deterministic; `max_iter` hard-capped at 10; terminal outcomes honored on
resume; iterations idempotent via `(job_id, iteration)` manifest keys.

| ID | Threat | Finding |
|---|---|---|
| TM-13 | **Path traversal via `video_id`** | Same class as TM-01: `state_dir / f"{video_id}.json"` and `manifest_path()` interpolate caller-supplied ids. Fix together with TM-01 (S-G2). Severity: **P0** (same rationale). |
| TM-14 | **Unauthenticated state forgery** | Loop state, checkpoints, and manifests carry no integrity protection. Anyone (or any compromised in-process code) can write `outcome="task_complete"` to make future loop invocations honor a fabricated terminal state, or inject manifest lines with a matching `job_id` to mark iterations already-done (skip execution). Blast radius: wrong results accepted as done; audit chain lies. Mitigation: integrity-protect evidence (hash-chain or HMAC keyed by an operator-held secret), verify on load, treat mismatch as `CheckpointCorrupt`-class loud failure; signing stays LATER-tier per PRD but cheap chaining can land early (S-G13 scope, staged). Status: documented gap (Constitution §7 "not tamper-proof"); planned. Severity: **P2** (requires local write access today; in-process actors have exactly that). |
| TM-15 | **Tighten-only gate is by convention** | `ADJUST_GATE` is designed tighten-only (SDD invariant 3) and `_apply` emits only collection-parameter axes, but nothing structurally prevents a future override key from loosening acceptance criteria; the FR-LOOP-003 axis-audit test is an explicit GAP scheduled Phase 4. Mitigation: land the override-key allowlist test + assert gate deltas ≥ 0 direction in the processor. Status: documented; test planned. Severity: **P2** (intent mutation is the highest-value target for an adversarial optimizer). |
| TM-16 | **Lesson poisoning (future)** | Phase 4 introduces lessons as strategy inputs. Adversarial captured data shapes metrics that shape lessons that shape future collection — a slow feedback injection channel. Not exploitable yet (no lesson store). Mitigation: lesson schema v1 MUST carry accept/reject verdicts, source-metric citations, and rejected lessons provably excluded (FR-SI-004); treat lesson inputs as untrusted data, never instructions. Status: planned (gated in ROADMAP Phase 4 exit). Severity: **P2-future**, must be closed before cross-run learning activates. |

### 2.5 Filesystem

Boundary intended: `data/`, `state/runs/`, `data/manifests/`, loop state dir;
plus `~/.tiktok-linkedin/` credential storage.

Enforcement: **none mechanical.** Constitution §2 admits "any script … can
write anywhere the process can write"; SECURITY.md lists explicit boundaries
as convention-only. Concrete escape vectors: TM-01/TM-13 id interpolation;
actors running in-process can simply open files (TM-03 class).

Mitigations & status: workspace jail (resolve every writer path under declared
roots; reject symlinks escaping the root; strict id charset) = planned S-G6;
until L3, the honest statement is: **filesystem confinement is documented,
not enforced.** Severity: **P0 aggregate** (via the concrete id vectors; the
generic in-process vector remains documented-limitation until L4).

### 2.6 Network / egress

Surface: TikTok fetches (API/DOM/route-intercept); CDP port probe
(`127.0.0.1:9222–9236`); vision-captcha POSTs screenshots to the configured
9Router endpoint using `LLM_KEY`; `requests` available to any code path.

| ID | Threat | Finding |
|---|---|---|
| TM-17 | **Unrestricted egress / exfiltration channel** | No allowlist anywhere. Any in-process code (or a poisoned provider module registered into the registry) can POST datasets/cookies/secrets to any endpoint. The existing vision-captcha path already ships page screenshots (potentially logged-in UI) to a third-party router — a sanctioned precedent for content egress, gated only by captcha presence (D-007 liability). Mitigation: egress allowlist enforced at the network layer (proxy/env-level blocklist default-deny) at L3/S-G7; until then, minimize: disable vision-captcha outside approved flows, document egress destinations honestly in TRANSPARENCY policy. Status: planned; currently documented-liability only. Severity: **P1**. |
| TM-18 | **Browser-session takeover blast radius** | CDP attach uses the *user's own logged-in browser profile* (attach-only by design). Any code driving that page acts with the user's full session across ALL sites open in it — not just TikTok. `route.capture` registers intercepts on `**/*`. Mitigation: dedicated automation profile separate from the user profile; scope route patterns to platform API hosts; keep attach-mode behind visible-browser human-in-the-loop (already agents.md policy). Status: partially documented; profile separation planned (S-G7). Severity: **P1**. |

### 2.7 Subprocess / process execution

No `subprocess`/`os.system` usage found in `src/` (verified by search).
Process spawning happens only inside libraries (Camoufox launches Firefox;
Playwright launches drivers). `process.execute` capability exists as vocabulary
and is consumed nowhere — correct posture. BrowserAgent tools execute JS inside
the *page* context (page.evaluate), which is sandboxed by the browser, not by
SDE — page-context JS is attacker-influenced only via scraped pages' DOM, and
evaluate calls use fixed scripts; risk is low but any future tool that builds
JS from data would create script injection. Mitigation: forbid data-derived JS
strings (review rule + lint grep). Status: enforced-by-absence + review rule
(documented). Severity: **P3**.

### 2.8 Secrets

| ID | Threat | Finding |
|---|---|---|
| TM-19 | **Plaintext session cookies at rest** | `export_tiktok_cookies.py` writes full TikTok cookies (incl. `sessionid`) as plaintext JSON to `~/.tiktok-linkedin/tiktok-cookies.json`; `_DEFAULT_COOKIE_FILES` also probes repo-relative `tiktok_cookies.json|.txt` — a world-readable or accidentally-committed cookie file is total account compromise. `TIKTOK_COOKIES` env var redirects the source arbitrarily. Mitigation: restrictive file perms (0600), refuse repo-relative cookie paths, keep `.gitignore` coverage, prefer persistent profile over exported copies; rotate on suspected leak. Status: partially mitigated (gitignore; grep gate covers passwords only, NOT cookie files). Severity: **P1** (PRODUCT.md §11 ranks credential compromise #3). |
| TM-20 | **Secrets in logs/summaries** | No redaction mechanism; print statements carry URLs, error text, cookie counts; checkpoints embed target URLs. Contract models are documented non-secret but no scanner enforces it (FR-SEC-004 GAP, scheduled Phase 6). Mitigation: pull the mechanical scanner forward to Phase 3 (S-G8) and redact at log/event boundaries. Severity: **P2**. |
| TM-21 | **LLM_KEY / env credentials** | `src/.env` holds router keys read at import time (`src/config.py`). Env-based credentials are permitted by the constitution; the risk is import-time availability to any in-process code (TM-03 class) and accidental commit. Mitigation: keep out of Git (already), scope to the captcha subsystem only, drop the global import-time constant in favor of lazy reads. Severity: **P2**. |

### 2.9 Datasets

Append-only tiered JSONL by convention; raw↔curated id traceability proven
(44/44 trace run). Gaps: writer-level tier separation not mechanically
refused (Constitution §12); records without ids always written (capped by item
budget, fine); curated tier filtered only by the 0.35 quality heuristic —
adversarial comment text passes trivially and persists (data poisoning of
downstream consumers; acceptable for raw-truth datasets, dangerous once lessons/
models consume them — see TM-16). Exfiltration-via-path covered by TM-01.
Mitigations: tier-refusal writer checks (planned), treat all captured content
as untrusted data forever. Severity: **P2** (integrity/poisoning), P3 (tier
separation).

### 2.10 Checkpoints

Atomic tmp+replace (no fsync — power-loss window documented); corrupt ⇒ loud
terminal failure (excellent); no lock (TM-08); no integrity auth (TM-14);
no actor binding on resume (TM-07). Aggregate severity: **P2**.

### 2.11 Plugins / connectors (`src/harness/*`, registry)

Experimental acquisition tooling reclassified by D-011. Findings:

| ID | Threat | Finding |
|---|---|---|
| TM-22 | **Registry shadowing / import side effects** | `Harness.register` prepends patterns (last registration wins); built-ins import at module import time. Any code that imports `src.harness.registry` and registers a broader pattern hijacks URL routing for existing providers. Third-party adapters get full process privileges (Constitution §14 DOCUMENTED ONLY). Mitigation: freeze builtin routes / require explicit precedence tokens; treat adapter registration as a privileged operation unavailable to submitted runs. Severity: **P2** (experimental surface; must not reach product entry points ungated — enforce via NFR-007-style import audits extended to the registry). |
| TM-23 | **Tool toolkit lacks capability mapping** | BrowserAgent tools (`browser.read/click/scroll/expand_replies/route.capture/api.fetch/media.enrich`, captcha solver) declare no capabilities and pass no gate; they are exactly the "excessive capability" class an adversarial agent would invoke. Mitigation: when this package becomes the TikTok actor's page source (Phase 3), each tool maps to a declared capability evaluated per action (feeds S-G3); stealth/captcha helpers stay behind REQUIRE_APPROVAL per D-007. Severity: **P1** at Phase 3 wiring time; P2 today (experimental). |

### 2.12 Future worktrees

Not implemented; no repository/worktree manipulation exists in code. Precondition
constraints when introduced: worktree creation is a capability (`worktree.create`)
granted only at L4+; worktree paths confined under an operator-declared root
(reuse S-G6 jail); each worktree inherits a strictly attenuated profile; branch/
push operations NEVER autonomous (destructive-op rule FR-SAF-003). Status:
planned constraints recorded now. Severity: n/a (design constraint).

### 2.13 Future sub-agents

Not implemented (D-001 keeps strategic autonomy external). When multi-agent work
begins (L5): sub-agents receive derived grants satisfying attenuation
(child grant ⊆ parent grant ∩ profile — S-G11), aggregate budgets across the
agent tree (S-G12), independent state namespaces (no shared mutable state
without ownership transfer), and the audit chain must attribute every action to
an agent identity. Anything less recreates TM-07/TM-08 at higher stakes.
Status: planned. Severity: n/a (design constraint).

### 2.14 Cross-cutting

| ID | Threat | Finding |
|---|---|---|
| TM-24 | **Harness-bypass via direct runtime use** | Direct `AcquisitionRuntime.run(actor, ctx, options)` skips validation and the gate. Sanctioned answer (FR-POL-004): trusted-internal path, same trust level as actor code; NOT an allowance for external callers. Enforcement is therefore *entry-point discipline*: CLI/import parity (Phase 3) must remove the practical incentive. Status: documented; S-G1 includes the import audit. Severity: **P1** (as exposure grows). |
| TM-25 | **Doc/code drift corrupts the safety narrative** | CURRENT-STATE.md predates the merged evaluator; SRS marks FR-POL-002..004 as GAP while tests exist. Docs that outrun OR trail reality both break the honest-labeling system (NFR-010) and would let a reviewer approve autonomy increases against a false picture. Mitigation: refresh CURRENT-STATE/SRS statuses citing `tests/test_policy_evaluator.py` + harness gate tests in the next docs PR; add a checklist item reconciling doc labels with merged tests. Severity: **P2**. |

---

## 3. Final output

### A. P0 / P1 threats (consolidated)

**P0**
1. **TM-01/TM-13 — Workspace escape via caller-controlled `job_id` / `video_id`
   path interpolation** (arbitrary-path write through sanctioned entry points).

**P1**
2. TM-02 — Policy gate opt-in; legacy CLI fully ungated.
3. TM-03 — Declarations ≠ actions; no action-level interception (host-trust
   actors can act beyond any declaration).
4. TM-04 — Budget escalation: unbounded config values, dead time/network axes,
   no aggregate loop budget.
5. TM-17 — Unrestricted egress incl. sanctioned screenshot-to-third-party
   channel.
6. TM-18 — CDP attach drives the user's real logged-in profile; route capture
   scoped to `**/*`.
7. TM-19 — Plaintext session-cookie export at rest + repo-relative cookie-file
   probing.
8. TM-23 — Experimental tool toolkit has no capability mapping (becomes P1 at
   Phase 3 wiring).
9. TM-24 — Practical harness bypass via direct runtime / dual CLI truths.

### B. Current bypasses (what an adversary could do *today*)

1. Submit `RunInput(job_id="../…")` (or `video_id=` into the improve loop) and
   create/overwrite JSON files outside the workspace — **no privilege needed
   beyond "can start a run."**
2. Start runs without any policy profile (default constructor) or via the
   legacy CLI — no evaluation ever occurs.
3. As in-process actor code: perform any action the host allows regardless of
   declarations (read env/keys, open sockets, write files) — the gate never
   sees actions.
4. Escalate work by config values (`max_items=10**9`) and rely on missing
   time/call budgets to stretch runs; multiply by `max_iter` loop rounds.
5. Pin another job's `job_id` with `resume=True` to adopt foreign cursor/state
   (cross-actor pollution).
6. With local write access: forge terminal loop outcomes or manifest iteration
   lines to skip work or fabricate success; edit checkpoints undetectably.
7. Export/read plaintext session cookies from `~/.tiktok-linkedin/` or a
   repo-relative cookie file; redirect cookie source via `TIKTOK_COOKIES`.
8. Ship captured content off-host through the sanctioned vision-captcha path
   (when triggered) or any direct socket as actor code.
9. Shadow provider routing by registering a broader URL pattern after import.

### C. Required security gates

Numbered S-gates referenced by the ladder (§1) and scheduled in `ROADMAP.md`
§Security gates:

| Gate | Name | Closes | Earliest phase |
|---|---|---|---|
| S-G1 | Mandatory deny-by-default gate on every sanctioned entry point (+ import audit; CLI unified) | TM-02, TM-24 | 2 |
| S-G2 | Identity-safe identifiers + workspace-rooted path resolution for jobs/loops/manifests | TM-01, TM-13 | 2 |
| S-G3 | Action↔capability linkage at engine insertion points 2 & 4 (per-action evaluation begins) | TM-03, TM-23 | 2→3 |
| S-G4 | Profile ceiling clamps on RunOptions/config budgets | TM-04 | 2 |
| S-G5 | Checkpoint↔actor binding on resume + single-writer lock + per-actor state namespaces | TM-07, TM-08 | 3 |
| S-G6 | Filesystem jail: all writers resolve under declared roots; symlink escape refusal | TM-01 residual, §2.5 | 3 |
| S-G7 | Egress allowlist + dedicated automation profile + route-pattern scoping + log redaction | TM-17, TM-18, TM-20 | 3→4 |
| S-G8 | Mechanical secret scanner over contract payloads (pulled forward from Phase 6) | TM-05, TM-20, TM-21 | 3 |
| S-G9 | Out-of-process actor boundary + serialized IPC contract tests (requires decision amendment) | TM-03 residual | 5/L4 |
| S-G10 | Wall-clock + network-call budget axes consumed pre-dispatch; kill-switch verb | TM-06 | 3 |
| S-G11 | Sub-agent grant attenuation proof | §2.13 | L5 |
| S-G12 | Aggregate budget axis across loops/agents | TM-04 residual | 4→L5 |
| S-G13 | Evidence integrity: hash-chained (later HMAC/signed) manifests/checkpoints; forged-state refusal | TM-14 | 4 |
| S-G14 | Lesson-integrity gate: accept/reject verdicts + untrusted-data treatment before cross-run learning | TM-15, TM-16 | 4 |

### D. Exact next implementation PR (PR #9)

**Title:** `feat(security): identity-safe identifiers, rooted paths, mandatory
gate default` — smallest enforceable step; targets S-G2 + part of S-G1/S-G4.
Scope:

1. `RunInput`: validate `job_id`/`run_id` against a strict slug charset
   (`^[a-z0-9][a-z0-9._-]{0,127}$` — reuse `is_valid_identifier` semantics);
   same for `SelfHealingPipeline.run(video_id=…)` and manifest path builders.
   Fail closed naming the field (additive; no legacy-format change needed since
   ids were always meant to be slugs).
2. `RunContext.create`: resolve final checkpoint/dataset paths and assert
   `os.path.commonpath([resolved, root]) == root` for both `state_dir` and
   `data_dir`; violation raises before any write.
3. `ActorHarness`: when `policy is None`, install the empty deny-all profile
   instead of silent trusted mode; retain an explicit keyword-only
   `trusted_operator=True` switch for the Phase 2 window (removed at CLI
   unification). Update `tests/test_actor_harness.py` accordingly.
4. Profile ceilings: `PolicyProfile` gains optional `max_*` clamps applied in
   `_resolve_options` (caller may lower, never exceed).
5. Secret-scanner stub (FR-SEC-004 pull-forward): pattern pass over
   `RunInput.payload/config` failing loudly on secret-like values.
6. Tests for every failure path (traversal ids, rooted-path refusal,
   gate-default denial, clamp behavior, scanner trip); update SRS/CURRENT-STATE
   labels with proofs.

Explicitly NOT in PR #9: egress control, locking, integrity chains,
out-of-process isolation (each owns a later gate).

### E. Is the architecture safe enough for increased autonomy?

**For current tactical autonomy (L0): yes, conditionally.** Budgets terminate,
fail-closed defaults dominate, evidence chains exist, and the newest layers
(evaluator, loop state) set the right tone. The residual risks are known and
mostly require local access or trusted-operator cooperation.

**For increased autonomy (L1 and beyond): no — not yet, and the ladder says why.**
Three specific blockers, in order:
1. The P0 workspace-escape class (TM-01/13) converts "submit a run" into
   "write anywhere" — intolerable the moment run submission is delegated.
2. Enforcement is optional at every sanctioned entry point (TM-02/24); an
   autonomy increase without a mandatory gate just automates the bypass.
3. Nothing constrains *actions*, only declarations (TM-03/04); more autonomy
   multiplies actions faster than declarations.

The architecture is *capable* of becoming safe: the ownership matrix, fail-closed
bias, insertion points, and additive-evolution rules give every gate above a
natural home without redesign. Promote levels only as the S-gates land, per
D-014. Do not widen exposure before S-G1 + S-G2 are green.
