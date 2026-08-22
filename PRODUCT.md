# Social Data Engine — Product Definition

| | |
|---|---|
| Version | 1.0 |
| Date | 2026-08-22 |
| Status | Product-definition baseline. Supersedes all implicit product assumptions carried by earlier PRs. |
| Evidence rule | PRs #1–#5 are **empirical evidence** of what can be built and what already works. They are **not** the specification. Where code and this document disagree, this document wins and the gap is tracked in `ROADMAP.md` / `CURRENT-STATE.md`. |
| Companion docs | `PRD.md` (requirements) · `SRS.md` (testable requirements) · `SDD.md` (architecture) · `ROADMAP.md` (phases) · `DECISIONS.md` (decision record incl. thesis challenge) · `CURRENT-STATE.md` (implementation map) |

---

## 1. Product identity

**Social Data Engine (SDE)** is a self-hosted, open-source, agent-pluggable
platform that turns a **declared acquisition intent** into a **verified
dataset with complete provenance** — executed within declared capabilities,
budgets, and policy — and that records **measurable evidence** of whether its
own operational changes actually improved results.

After the thesis challenge (§5, `DECISIONS.md` Q1–Q8), the precise identity is:

> SDE is a **bounded data-capability substrate**, not an autonomous agent.
> Autonomy inside SDE is tactical and bounded (repair, resume, parameter
> selection within a declared intent). Strategic autonomy — what to collect,
> when, and why — deliberately stays with the external agent or human.

One sentence: *declare what you need; get back a dataset you can audit,
produced inside limits you set, with evidence of what worked.*

## 2. Primary user

**Agent-system / automation builders** — humans or frameworks (Hermes, Claude,
Codex, custom runners) that need social data as *infrastructure*: reproducible,
inspectable, bounded, pluggable. The machine caller is first-class: every
entry point must work identically via import and CLI, with serialized,
versioned contracts (`RunInput`, `RunSummary`).

Secondary users:
- **Operators** who run collections and need resumable, bounded jobs.
- **Auditors** who must reconstruct how any stored record was acquired.
- **Platform contributors** who add providers by implementing an actor against
  a stable contract, without touching the runtime.

## 3. Problem

Agents can reason, but social-data supply is broken for them:

1. **Official APIs** are expensive, rate-limited, and structurally incomplete
   (threaded comment trees are frequently unavailable).
2. **Ad-hoc scraping** is brittle, opaque, unbounded, and unsafe to hand to an
   autonomous process: no budgets, no resume, no evidence.
3. **Existing tools return data without provenance** — failures are silent,
   retries are unmeasured, "it worked" is unverifiable.
4. **Integration cost**: wiring acquisition into an agent loop requires bespoke
   glue per platform, per run.

SDE attacks problems 2–4 directly. It does not attempt to solve problem 1
(official-API economics) beyond supporting API-based providers when they exist.

## 4. Jobs to be done

| ID | Job |
|---|---|
| JTBD-1 | "When my agent needs a social dataset, I want to **declare what I need** and receive a verified dataset with provenance — without building per-platform scrapers." |
| JTBD-2 | "When a collection partially fails, I want it to **resume and repair within bounds**, and be told exactly what changed." |
| JTBD-3 | "When I tune collection strategy, I want **evidence that a change improved coverage/quality** — not just a log that something ran." |
| JTBD-4 | "When something goes wrong, I want to **reconstruct what was collected, from where, by which actor, under which declared permissions**." |
| JTBD-5 | "When a new platform appears, I want to implement an actor/provider against a stable contract **with zero runtime modification**, proven by tests." |

## 5. Thesis — original, challenge, revision

Original thesis: *"self-hosted, open-source, agent-pluggable autonomous data
capability platform."*

The challenge (full record in `DECISIONS.md`) found one word wrong:

| Challenge question | Verdict |
|---|---|
| Q1 Autonomous platform vs substrate? | **Substrate.** Every safety mechanism in the codebase (budgets, termination taxonomy, checkpoints, deterministic planners) is *bounding* machinery, not autonomy machinery. Autonomy belongs mostly outside. |
| Q2 Which autonomy is internal? | Tactical only: retry, resume, repair loops ≤ `max_iter`, parameter adjustment **within** a declared intent. Strategic autonomy stays external. |
| Q3 Self-improvement core or optional? | **Core promise, staged delivery.** v1 = measured tactical repair (exists). Cross-run strategy learning = optional later subsystem. |
| Q4 Loops now or loop-capable runtime? | Runtime is already loop-capable; only bounded internal loops ship. No scheduler, ever (non-goal). |
| Q5 Smallest coherent product? | Declared intent → enforced-capability, budgeted execution → verified dataset + provenance + summary. One provider suffices; enforcement and manifest-v1 are the missing pieces. |
| Q6 What would make SDE unnecessary? | Open platform APIs, provenance-backed scraping vendors, or framework-native data adapters. See §10. |
| Q7 Largest security risk as autonomy grows? | Ranked list in §11; today's top gap: capability declarations are advisory (no evaluator). |
| Q8 Differentiated vs convenient? | Differentiated: provenance chain, bounded-by-construction, falsifiable improvement, agent-shaped contracts, self-hosting. Convenient/commodity: per-platform coverage, JSONL, dedup scores. |

**Revised thesis:** SDE is an *agent-pluggable, self-hosted platform for
bounded social-data acquisition* whose differentiators are provenance,
boundedness, and **falsifiable operational self-improvement** — with autonomy
kept tactical by design, not by omission.

## 6. Differentiation (honest)

Differentiated — these are hard for commodities to copy because they are
structural, not features:

1. **Provenance-first datasets.** Every curated row traces to raw capture;
   runs persist actor identity, declared capabilities, budgets, outcome, and
   lifecycle (`src/runtime/engine.py`, manifests).
2. **Bounded by construction.** Budgets must bound ≥ 1 axis (`ExecutionBudget`
   refuses fully-unbounded), termination always classifies through a known
   taxonomy, corrupt checkpoints fail loudly instead of masquerading as fresh
   runs.
3. **Falsifiable self-improvement.** Improvement claims require recorded
   before/after metric deltas (`ImprovementPlanner.MIN_IMPROVEMENT_DELTA`);
   memory writes alone never count as learning.
4. **Agent-shaped contract surface.** Serialized `RunInput`/`RunSummary`,
   identity binding, structural capability validation — designed for machine
   callers, proven by two actors on one unmodified runtime (PR #5 tests).
5. **Self-hosted.** Credentials and data never leave operator control.

Commodity (kept minimal, not claimed as differentiation): TikTok coverage per
se, JSONL storage, dedup/quality heuristics, CLI ergonomics.

## 7. Core capabilities

| Capability | Status in product | Notes |
|---|---|---|
| Social data acquisition | Core | Provider-pluggable; TikTok is the reference provider |
| Actor / harness execution | Core | Contract shipped PR #5; enforcement pending |
| Bounded runtime | Core | Budgets, checkpoint/resume, termination, metrics |
| Dataset management | Core | Tiered JSONL, canonical identity, byte-compat |
| Processing | Core (minimal) | Normalize → dedup → quality gate; nothing more until needed |
| Provenance / transparency | Core | Manifests + summaries; schema-v1 formalization pending |
| Policy / capability | Core | Vocabulary shipped; evaluator v0 required before wider exposure |
| Safety / security | Core (gates) | Live-test gate, credential rules, fail-closed parsing |
| Self-improvement | Core, staged | Measured tactical loop now; cross-run learning optional later |
| Preferences | Optional later | Explicit config only in v1; store deferred |
| External-agent integration | Core | Stable serialized schemas + CLI/import parity |

**Learning taxonomy (explicit).** SDE's core self-improvement is
**operational learning**: bounded, measured adjustment of execution parameters
within a declared intent, evidenced per iteration in manifests. Three adjacent
categories are deliberately distinct: **cross-run strategy optimization** is
LATER (gated on Phase 4 evidence), **preference learning** is LATER behind an
audited preference store (nothing infers preferences today), and **model
improvement** (training/fine-tuning anything) is OUT entirely.

## 8. Explicit non-goals

Not buildable, not claimable, not on any roadmap phase:

1. General-purpose chatbot or assistant surface.
2. Frontier-model or model-training platform.
3. Replacement for general web search.
4. Scheduler-first product; autonomous scheduling engine; cron-like orchestration.
5. Unbounded recursive/self-modifying loop engines ("autonomous AGI loops").6. Sandboxing / process isolation of actors — OUT, not merely deferred; actors
   run in-process with host trust (documented limitation, FR-SEC-001). Reversal
   requires a new decision record, not roadmap drift.
7. Anti-detection arms race as a feature. Existing stealth/captcha helpers
   (`src/harness/human.py`) are treated as a **liability gated behind future
   approval flow**, not a selling point (`DECISIONS.md` D-007).
8. Binding to any named agent framework (Hermes/Claude/Codex are consumers via
   generic interfaces, never dependencies).
9. Claims of intelligence gains from self-improvement; "learning" without
   measured deltas.
10. Cloud service / multi-tenancy / dashboards.
11. Generic non-social web scraping — SDE is scoped to social platforms; a new
    provider class outside social data requires a decision record.
12. Real-time / streaming subscription collection — the model is poll +
    resume; continuous observation loops are OUT.

## 9. Example user journeys

**J1 — Agent developer (primary).**
An agent needs comments for a TikTok video. It constructs a `RunInput`
(actor id/version, target URL, config axes), calls the harness (import) or CLI.
SDE validates identity and declarations, executes through the bounded runtime,
and returns a `RunSummary` (lifecycle, coverage, quality, termination reason)
plus tiered JSONL datasets and a provenance manifest. On partial failure the
agent re-submits the same `job_id`; execution resumes without duplicates.

**J2 — Operator.**
A collection stalls at 40 % coverage. The internal self-healing loop
(`SelfHealingPipeline`, `max_iter=3`) plans deterministic remediations,
re-collects, measures the delta, stops on stability or budget exhaustion, and
appends each iteration to `data/manifests/<video_id>.improve.jsonl`. The
operator reads one manifest line and knows what was tried and whether it worked.

**J3 — Auditor.**
Given a `video_id`, the auditor walks raw → curated → normalized tiers, checks
`raw.id == curated.id` linkage, reads the run summary (actor version,
capabilities, budgets, termination reason), and can state exactly how the
dataset was produced and under what limits.

**J4 — Platform contributor.**
A contributor implements `SecondPlatformActor(AcquisitionActor)` with its own
provider module, declares capabilities, runs the existing harness/runtime
unmodified. Tests demonstrate execution, resume, and distinguishable
per-version metadata — the acceptance proof for "pluggable."

## 10. Existential risk (what makes SDE unnecessary)

SDE collapses to thin glue if any of these commoditize:
1. Platforms open stable APIs exposing full threaded-comment trees at sane cost.
2. Scraping vendors offer provenance-backed, threaded extraction.
3. Agent frameworks ship native data-acquisition adapters with provenance.

Mitigation is scope discipline: keep the differentiators structural
(provenance, boundedness, falsifiable improvement), keep the codebase small
(stdlib-first), and accept abandonment-cost asymmetry as a feature — if SDE
becomes unnecessary because the world got better, it succeeded.

## 11. Claim discipline & risk ranking

Claims SDE may never make: AGI-adjacent anything; "learns" without a recorded
metric delta; stealth/evasion as a capability; unsupervised autonomy.

Security risk ranking as autonomy increases (drives roadmap order):
1. **Capability–policy gap** — declarations exist but nothing evaluates them (today's state).
2. Budget escalation / self-modified parameters without audit.
3. Credential/session compromise (cookie stores, browser profiles).
4. Lesson poisoning — adversarial input shaping future strategy.
5. Dataset-path exfiltration via unrestricted write targets.

Items 1 and 2 gate every autonomy increase; see `PRD.md` Policy MUSTs and
`ROADMAP.md` Phase 2.
