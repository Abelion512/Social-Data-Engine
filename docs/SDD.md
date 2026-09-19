# Social Data Engine — System Design Document

Version 1.0 · 2026-08-22 · Implements `PRODUCT.md`; requirement refs → `SRS.md`.
This document defines the *intended* architecture. Where today's code deviates,
the deviation is named and tracked (§6 Deviations, `ROADMAP.md` phases).

---

## 1. Layer diagram

```
User / Agent                      ← owns INTENT (external, never internal)
   │  RunInput (serialized: target, config, actor identity)
   ▼
SDE Interface                     ← CLI + import entry points; TRANSLATION only
   │  validated request
   ▼
Harness / Actor                   ← ACTOR METADATA · DECLARED CAPABILITIES ·
   │                                input validation · lifecycle bookkeeping
   │  gated execution authorization
   ▼
Policy / Capability               ← VOCABULARY (shipped) · EVALUATOR v0 (Phase 2)
   │                                deny-by-default decision before any action
   ▼
Bounded Runtime / Loop            ← BUDGETS · RETRY · CHECKPOINT · TERMINATION ·
   │                                METRICS · bounded internal loops
   │  provider-neutral execution request (page source injected)
   ▼
Provider / Acquisition            ← FETCH · PARSE · SESSION (platform-specific)
   │  raw observations
   ▼
Processing                        ← NORMALIZE · DEDUP · QUALITY GATE
   │  curated records
   ▼
Dataset                           ← DATA IDENTITY · tiered append-only JSONL
   │
   ▼
Provenance / Audit                ← EVIDENCE CHAIN: manifests, summaries, traces
   │
   ▼
Result / Learned Op. Knowledge    ← LESSONS + MEASURED DELTAS (improve subsystem)
```

Reading rule: **requests flow down; evidence flows up.** No layer may skip the
policy gate once it exists, and no layer may consume evidence it did not
receive from below (no fabricated provenance).

## 2. Ownership matrix

The single most important table in SDE design. "Owner" = the only layer
allowed to decide; every other layer must receive, not invent.

| Concern | Owner | Explicitly NOT owned by | SRS |
|---|---|---|---|
| Intent (what to collect, from where, why) | External agent/user; enters immutably as `RunInput.target_url`+`payload` | Any SDE layer — intent is never invented, altered, or inferred internally | FR-LOOP-003 |
| Execution strategy (how, within intent) | Split: **Actor** = page-level tactics; **Runtime** = ordering/retry within budgets; **Improve** = future-parameter proposals only | Harness (must stay thin); never crosses intent | FR-HAR-003 |
| Provider behavior (fetch/parse/session) | Provider module behind the actor's page source | Runtime (provider-blind, test-enforced), Harness | NFR-007 |
| Budgets (items/pages/retries/time/calls) | Runtime, from `RunOptions`/`ExecutionBudget` | Actor, Provider, Improve (may propose, never grant) | FR-RUN-002 |
| Policy (vocabulary + enforcement) | Vocabulary: `src/policy/models.py`; Enforcement: evaluator v0; Gating: Harness | Providers; Improve | FR-POL-002..004 |
| Data identity (canonical ids, job/run ids) | Dataset layer + `RunContext` | Actors (may not re-key data) | FR-DAT-001 |
| Persistence (checkpoints, datasets, manifests) | Runtime (checkpoints), Dataset (records), Audit (manifests) | Providers (no direct checkpoint writes) | FR-ACQ-003 |
| Learning (lessons, measured deltas) | Improve subsystem — records evidence, proposes parameters; application happens only as new explicit config | Everything else; never silently | FR-SI-003 |
| User preferences | External (v1): explicit `RunInput.config`/CLI only; preference store LATER, outside improve | Improve (may propose, never infer) | FR-PREF-001 |

## 3. Boundary invariants (test-enforceable)

1. **Provider-blind runtime.** `src/runtime/` must not import `src/providers/*`.
   *(Held today structurally; explicit audit test scheduled Phase 2 — NFR-007.)*
2. **Harness adds no execution wrapper.** Summary type via harness == via
   direct runtime; harness holds no cross-run state. *(Proven, PR #5 tests.)*
3. **Intent immutability.** Loop/improve override surfaces may contain
   collection-parameter axes only — never target/identity. *(Held by
   construction; explicit test scheduled Phase 4 — FR-LOOP-003.)* Gate ruling:
   autonomous threshold adjustment (`ADJUST_GATE`) may only **tighten** a
   quality gate — loosening an acceptance criterion changes what the intent's
   output must satisfy, which is intent mutation and requires external config.
4. **Evidence before claim.** No code path may report "improved" without a
   recorded delta; docs may not outrun labels. *(NFR-010.)*
5. **Fail-closed everywhere.** Unknown states classify to
   `PERMANENT_FAILURE`; corrupt checkpoints are explicit terminal recovery
   failures; evaluator exceptions will resolve DENY. *(Held in scope of
   runtime; extends with evaluator.)*
6. **Additive evolution.** Legacy checkpoint/dataset fields are never renamed;
   new reasons/states are additive. *(NFR-009.)*

## 4. Module map (current → intended)

| Layer | Today | Intended change |
|---|---|---|
| Interface | `run.sh`, `src/tiktok_linkedin.py` CLI, import paths | Unify CLI onto harness path; exit-code taxonomy (Phase 3/6) |
| Harness/Actor | `src/runtime/harness.py` (`RunInput`, `ActorHarness`), `src/runtime/actor.py` | Dry-run mode; evaluator gate insertion point (Phase 2) |
| Policy | `src/policy/models.py` (vocabulary, budget models, `PolicyDecision` unused) | Add `src/policy/evaluator.py`; wire gate; `policy_denied` reason (Phase 2) |
| Runtime/Loop | `src/runtime/{engine,checkpoint,context,dataset,metrics,state,termination}.py`; `src/pipeline/improve.py` | Consume time/network budget axes; lifecycle already additive (Phase 2/4) |
| Provider | `src/providers/tiktok.py`, `tiktok_actor.py` (contract surface, injected page source); `linkedin.py` stub | Wire production browser page source into the actor (Phase 3) |
| Processing | `src/pipeline/{stages,dedup,quality,identity}.py` | No structural change |
| Dataset | `src/runtime/dataset.py`, tiered `data/` dirs | Summarize dataset location; manifest schema v1 (Phase 2) |
| Provenance | `src/export/manifest.py`, improve manifests, run summaries | Verification command; schema version stamp (Phase 2) |
| Learning | `src/pipeline/improve.py` (planner + bounded loop + manifests) | Lesson schema v1 + delta reports (Phase 4) |
| Experimental | `src/harness/` (BrowserAgent, tools, registry, human) | Reclassified as acquisition tooling behind a TikTok actor page source (Phase 3); stealth/captcha paths gated per D-007 |

## 5. Key data contracts

- **RunInput** (`src/runtime/harness.py`) — frozen, fail-closed, serializable;
  identity binding to the actor object; config restricted to `RunOptions` axes.
- **RunSummary** (`src/runtime/engine.py`) — outcome, termination reason,
  lifecycle, counts, coverage, quality, durations, actor provenance.
- **ExecutionBudget** (`src/policy/models.py`) — must bound ≥ 1 axis;
  fully-unbounded is unrepresentable.
- **Capability / CapabilityRequest** — namespaced verbs + evaluation context
  (non-secret metadata only).
- **Manifests** — `data/manifests/<video_id>.improve.jsonl` (improve
  iterations); run/checkpoint provenance in `state/runs` + summaries.
- **Minimal external-agent interface** — one `RunInput` in → one `RunSummary`
  out plus discoverable dataset paths and manifest path. Dataset-location
  discovery is contractual (FR-DAT-003) precisely so agents never need
  internal filesystem knowledge.

## 6. Deviations from this design (honest list)

| Deviation | Impact | Resolution |
|---|---|---|
| Capability declarations advisory (no evaluator) | Contract's central promise unenforced; top security risk | Phase 2 (first roadmap item) |
| Provenance not emitted universally at write time | JTBD-4 partially manual | Phase 2 manifest schema v1 |
| CLI predates harness contract | Two entry truths | Phase 3 wiring |
| Dual "harness" naming (`src/harness/` vs ActorHarness) | Confusion risk | Docs fix now (D-011); physical rename Phase 3 |
| Actors in-process with host trust | Third-party actors unsafe | Documented limitation; sandboxing OUT by decision |
| Not pip-installable | Agent adoption friction | Phase 6 packaging (D-012) |
| Looped runs multiply budgets: aggregate work = `max_iter` × per-run budget; no declared aggregate bound exists | "Limits you set" is weaker for looped runs than single runs | Documented limitation; declared aggregate budget axis evaluated in Phase 4 |

## 7. Evolution rules

- New layer responsibilities require a `DECISIONS.md` entry and SRS additions —
  never a silent refactor.
- Autonomy increases are gated: evaluator v0 → lesson integrity (Phase 4) →
  then, and only then, wider exposure (Phase 6).
- Anything that would make the runtime import providers, the harness scrape,
  or the improve loop set goals is rejected by review as a boundary violation,
  regardless of convenience.
