# Social Data Engine — Decision Record

Decisions from the product interview and the thesis-challenge session.
Each records context, decision, consequences, and rejected alternatives.
Supersedes any contrary implication in earlier docs; PRs are evidence, not authority.

Status: `ADOPTED` (binding) / `DEFERRED` (revisit trigger defined).

---

## A. Interview decisions

### D-001 — Autonomy boundary: tactical inside, strategic outside — ADOPTED
**Context.** Original thesis said "autonomous data capability platform." The
codebase's entire safety apparatus (hard item caps, page caps, retry budgets,
termination taxonomy, `max_iter=3` loops, deterministic planners) is bounding
machinery — evidence that SDE was already designed as a bounded executor.
**Decision.** Strategic autonomy (what/when/why to collect, lesson acceptance)
belongs to the external agent/human. Internal autonomy is tactical only:
retry, resume, repair, parameter adjustment **within a declared intent**, all
budgeted. SDE may change *how* an intent is achieved; never *what* the intent is.
**Consequences.** No scheduler, no goal invention, no self-set objectives — ever.
Marketing may not say "autonomous agent platform."
**Alternatives rejected.** (a) Full internal autonomy: contradicts every
safety mechanism built so far; unacceptable risk surface. (b) Pure dumb
library with zero internal adaptation: throws away working checkpoint/resume
and bounded repair loops that are real differentiators.

### D-002 — Self-improvement = falsifiable measured deltas — ADOPTED
**Context.** Product insight: Run N → observe → detect → record lesson →
modify strategy → Run N+1 → **measure whether performance improved**. Storing
memory is not learning.
**Decision.** An improvement claim requires recorded before/after deltas on
defined metrics (`coverage`, `avg_quality`, `dup_rate`) at plan granularity.
`ImprovementPlanner.MIN_IMPROVEMENT_DELTA = 0.05` exists today for loop
continuation; lesson-level delta reporting is specified in `SRS.md`
(FR-SI-003) and currently a gap.
**Consequences.** Every "improved" statement in docs/dashboards must cite a
manifest line. Memory writes without deltas are bookkeeping, explicitly not learning.
**Rejected.** Treating manifest-existence as improvement proof (unfalsifiable).

### D-003 — Loops: bounded, internal, runtime stays loop-capable — ADOPTED
**Context.** Question: build loops now or only a loop-capable runtime?
The runtime is already loop-capable (checkpoint/resume/budgets) and one bounded
loop ships (`SelfHealingPipeline`).
**Decision.** Ship bounded internal loops only. The runtime contract remains
loop-ready without embedding scheduling. No scheduler subsystem in any phase.
**Consequences.** External agents remain the schedulers; SDE exposes idempotent,
resumable run units instead of daemons.

### D-004 — Provider-independent runtime is core, proven by tests — ADOPTED
**Context.** PR #3/#4 built `AcquisitionRuntime`; PR #5 proved a fake actor and
a TikTok-shaped actor execute through the *same* runtime instance with resume
intact and zero runtime edits.
**Decision.** Runtime stays provider-blind as a test-enforced invariant;
platform logic lives in providers/actors only.
**Consequences.** Any PR that adds provider imports to `src/runtime/engine.py`
fails review by constitution, not just taste.

### D-005 — Provenance is a guarantee, not a feature — ADOPTED
**Context.** Constitution §8 labels provenance PARTIALLY IMPLEMENTED:
manifest writing lives in the improve loop, not universally at dataset append.
**Decision.** Provenance completeness (manifest schema v1, emitted at write
time, verifiable by command) is a MUST requirement gating Phase 2 exit —
before any autonomy increase or second-provider expansion.
**Rejected.** Best-effort provenance ("where possible") — destroys JTBD-4.

### D-006 — Vocabulary before evaluator; evaluator before autonomy growth — ADOPTED
**Context.** Capability declarations are structural configuration only; nothing
evaluates them (Constitution §5 DOCUMENTED ONLY). This is the top-ranked
security risk as autonomy increases (PRODUCT.md §11).
**Decision.** Policy evaluator v0 (deny-by-default, deterministic,
`PolicyDecision`-consuming) is required before exposing harness execution to
external agents beyond the current trusted-operator mode, and before any loop
or budget parameter becomes externally configurable.
**Consequences.** ROADMAP Phase 2 is ordered before second-provider and
integration phases.

### D-007 — Stealth/captcha automation is a liability, not a feature — ADOPTED
**Context.** `src/harness/human.py` implements stealth fingerprinting, humanized
delays, captcha pre-resolution. Constitution §10 already names this class as
needing approval once the gate exists; `policies/ACCEPTABLE-USE.md` governs it.
**Decision.** These helpers are never documented or marketed as capabilities.
They are gated code paths that must sit behind `REQUIRE_APPROVAL` when the
policy evaluator lands, and their presence must be declared in actor
capability declarations (`browser.automate`) once evaluation exists. Until
then, they restrict which deployment modes are supported (visible browser,
human-in-loop login), per `agents.md`.
**Rejected.** Removing them now (breaks live collection); showcasing them
(reputational + ToS risk).

### D-008 — Two distinct human controls, never conflated — ADOPTED
**Context.** Live-test-before-merge (workflow control, per `agents.md`) vs
runtime Human Override verbs (stop/approve/deny/escalate over an executing
action — Constitution §10, PLANNED).
**Decision.** Keep both, name both, implement separately. Workflow gates do
not grant runtime override authority and vice versa.

### D-009 — External agents are first-class via generic contracts — ADOPTED
**Context.** Consumers named (Hermes, Mark, Claude, Codex) but product must
not bind to any of them.
**Decision.** Integration happens through versioned serialized schemas
(`RunInput`, `RunSummary`, manifests) plus CLI/import parity. Named agents get
*examples*, never hard dependencies.
**Deferred:** publishing schemas as standalone artifacts (Phase 6).

### D-010 — Stdlib-first, fail-closed models, no schema frameworks — ADOPTED
**Context.** Entire policy/runtime layer is stdlib dataclasses with fail-closed
validation and explicit serialization; repo has `requirements.txt` only, no
framework lock-in.
**Decision.** Continue. "Do NOT create a large schema framework" (PR #5 brief)
generalizes to the product: contracts stay small, serializable, versioned.

---

## B. Thesis challenge record

| # | Question | Verdict | Evidence |
|---|---|---|---|
| Q1 | Autonomous platform or external-agent substrate? | **Substrate with tactical autonomy.** | All safety code bounds rather than drives; user profile is agent builders who already plan. |
| Q2 | Which autonomy inside vs outside? | Inside: retry/resume/repair/parameterization within declared intent+budgets. Outside: goal selection, scheduling, preference authorship, lesson acceptance. | `SelfHealingPipeline` mutates params, never targets; `RunInput` carries intent immutably. |
| Q3 | Self-improvement core or optional? | Core promise, staged: v1 tactical measured repair (shipped); cross-run strategy learning optional-later. | improve.py loop + manifests exist; cross-run store does not. |
| Q4 | Loops now or loop-capable runtime? | Both already true minimally; ship nothing beyond bounded internal loops. | engine.py budget machinery; improve.py `max_iter`. |
| Q5 | Smallest coherent product? | Declared intent → enforced-capability budgeted run → verified dataset + summary + provenance; one provider; resumable. Missing vs today: evaluator v0, manifest schema v1, packaging. | Gap analysis in CURRENT-STATE.md. |
| Q6 | Obsolescence triggers? | Open platform APIs; provenance-backed scraping vendors; framework-native adapters. Response: keep differentiators structural, keep code small. | PRODUCT.md §10. |
| Q7 | Biggest security risk as autonomy grows? | 1) capability–policy gap (current), 2) budget/self-modification without audit, 3) credential/session compromise, 4) lesson poisoning, 5) dataset-path exfiltration. | Constitution §1/§5/§15 DOCUMENTED ONLY; human.py audit. |
| Q8 | Differentiated vs merely convenient? | Differentiated: provenance chain, bounded-by-construction, falsifiable improvement, agent-shaped contracts, self-hosting. Convenient: TikTok coverage, JSONL, dedup scores, CLI sugar. | PRODUCT.md §6. |

## C. Structural findings from this session

### D-011 — Naming collision acknowledged, rename deferred — ADOPTED
`src/harness/` (BrowserAgent tool registry, experimental acquisition tooling)
collides with `src/runtime/harness.py` (`ActorHarness`, the product contract).
**Decision.** In all product docs, "harness" means `ActorHarness`. The
BrowserAgent package is reclassified as an *experimental acquisition tool*
that must eventually become a page-source implementation behind a TikTok actor,
not a competing architecture. Physical rename deferred to avoid churn while
PR #5 is open; tracked in ROADMAP Phase 3.

### D-012 — Not pip-installable is a product gap, not a style choice — ADOPTED
No `pyproject.toml`; consumers cannot `pip install`. Packaging enters the
roadmap (Phase 6) as a SHOULD-tier integration requirement because agents need
installable surfaces. Deferred until after enforcement/provenance phases
(value order: trustworthy > distributable).

### D-013 — PRs are evidence, not specification — ADOPTED
Where existing architecture serves the spec, it is kept (runtime, checkpoint
semantics). Where it conflicts (advisory-only capabilities, non-universal
provenance, dual "harness" meanings), the spec wins and gaps are scheduled —
never silently relabeled as design intent.

### D-014 — The security maturity ladder binds every autonomy increase — ADOPTED (PR #8)
**Context.** PR #8 audited SDE against an adversarial model/actor and produced
`docs/SECURITY-THREAT-MODEL.md` (25 tracked threats TM-01..TM-25) and
`docs/architecture/CONTAINMENT.md`. Findings: one P0 class — caller-controlled
`job_id`/`video_id` path interpolation lets a submitted run write outside the
workspace through sanctioned entry points (TM-01/TM-13) — plus P1 gaps: the
policy gate is opt-in and the legacy CLI runs ungated (TM-02/TM-24),
declarations are never matched to actions (TM-03), budgets have no ceilings or
time/call axes (TM-04), egress is unrestricted including a sanctioned screenshot-
to-third-party channel (TM-17), CDP attach drives the operator's real logged-in
profile (TM-18), and session cookies persist as plaintext exports (TM-19).
**Decision.** (1) Autonomy/exposure increases are gated by the security maturity
ladder L0–L5 defined in those documents; promotion requires the level's S-gates
green plus a recorded promotion note — roadmap ordering alone no longer
authorizes wider exposure. (2) No widening of exposure before **S-G1 (mandatory
deny-by-default gate on all sanctioned entry points) and S-G2 (identity-safe
identifiers + workspace-rooted path resolution)** land. (3) L4 (process/sandbox
isolation) remains unreachable without explicitly amending PRODUCT.md §8
non-goal #6; containment docs may describe it but no roadmap phase enacts it.
(4) Controls must be enforced outside the model's reasoning — constructors,
evaluator, path resolution, OS boundaries — never prompts or conventions.
**Consequences.** PR #9 (next implementation PR) is pinned to S-G1+S-G2+ceiling
clamps + secret-scanner stub. `ROADMAP.md` carries the S-gate schedule. Doc/code
drift found during the audit (CURRENT-STATE/SRS lag merged evaluator work) must
be reconciled under NFR-010 discipline.
**Rejected.** (a) Treating the threat model as informational only — leaves the
P0 escape open exactly when exposure grows. (b) Jumping straight to sandboxing —
contradicts an explicit non-goal and skips cheap high-value gates. (c) Fixing
the P0 by documentation — violates the outside-the-model-reasoning rule.

### D-015 — Upgrade gates, asset classification, and trust boundaries are normative — ADOPTED (PR #8 review fixes)
**Context.** Review of PR #8 accepted the threat-model direction but found the
ladder descriptive rather than enforceable: nothing stopped a future
contributor from building L5 machinery over an unfinished L3, "protect
secrets" had no per-asset meaning, and prose alone could not show where trust
ends for the next developer.
**Decision.** Three additions to `docs/SECURITY-THREAT-MODEL.md` are binding.
(1) **§1.1 upgrade law:** each transition L0→L1 … L4→L5 names its required
enforcement class and owning S-gates; implementing or scheduling level N+1
mechanisms while level N's gates lack green tests is a merge-blocking
violation; promotions are recorded human decisions citing those tests;
demotions require an incident record; exposure never widens with an open P0.
(2) **§0.1 asset classification:** every protected asset carries a sensitivity
(Critical / Medium / integrity-tier), an allowed-access rule, and storage
handling; Critical assets (`LLM_KEY`, TikTok session cookies, the operator's
logged-in browser profile) never enter payloads, prompts, logs, or datasets in
any representation; evidence assets rank integrity above confidentiality
(D-005); captured content is Medium and permanently untrusted data.
(3) **§0.2 trust boundary diagram:** UNTRUSTED (actor reasoning, submitted
inputs, plugin input, scraped data) → CONTROLLED (ActorHarness,
PolicyEvaluator, AcquisitionRuntime, loops, path resolution) → TRUSTED
(storage, secrets, execution environment), with one-way crossing rules; until
L2/L3 the controlled/trusted separation for in-process actors is logical, not
mechanical, and must stay labeled as such (TM-03).
**Consequences.** Reviewers gain three concrete refusal criteria: missing gate
class for a transition, unclassified new assets, and any control that assumes
an actor can be trusted with host reach. CONTAINMENT.md §1 and ROADMAP.md's
security overlay carry pointers so the law is met where work is planned. No
code changes in this PR; enforcement itself remains owned by the S-gates.
**Rejected.** (a) Keeping the ladder prose-only — reviewers would keep
relitigating whether autonomy increases are premature instead of checking a
gate table. (b) Classifying assets ad hoc inside each threat row — access
rules must be checkable in one place before code lands. (c) Mermaid/image
diagrams — ASCII survives diffs, terminal review, and grep.
