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
