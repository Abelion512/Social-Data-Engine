# Policy Architecture (future enforcement pipeline)

**Status: PLANNED.** This document defines where policy enforcement will
integrate. It is not implemented. What exists today is the vocabulary in
`src/policy/models.py` (`Capability`, `CapabilityRequest`, `PolicyDecision`,
`ExecutionBudget`) and nothing else — no evaluator, no sandbox, no approval
workflow.

## The pipeline

```text
Actor / Harness            declares required Capabilities at registration
      ↓
Capability declaration     static, versioned list of Capability names
      ↓
Policy evaluation          pure function: CapabilityRequest → PolicyDecision
      ↓
ALLOW / DENY / REQUIRE_APPROVAL
      ↓
Execution budget           finite limits checked BEFORE each action
      ↓
Action                     one fetch / write / call
      ↓
Audit / Provenance event   append-only record of what happened + why
      ↓
Loop decision              continue · checkpoint+resume · terminate
      ↓
Policy evaluation again    re-checked every iteration — grants never persist past an iteration
```

## Insertion points in `src/runtime/` (exact symbols)

| # | Where | What happens |
|---|---|---|
| 1 | `AcquisitionActor` subclasses (`src/runtime/actor.py`) | Actor declares `required_capabilities()` — e.g. `network.fetch`, `browser.automate`. Providers declare; the runtime never hardcodes platform verbs. |
| 2 | `AcquisitionRuntime.run()` loop (`src/runtime/engine.py`), immediately before `actor.fetch_page(...)` (step 1 of the loop) | Evaluate `CapabilityRequest(actor_id=actor.provider_name, capability=..., resource=ctx.target, purpose=...)`. `DENY` ⇒ terminate with a new reason string (see below). `REQUIRE_APPROVAL` ⇒ checkpoint + suspend. |
| 3 | `RunOptions` (`src/runtime/engine.py`) | Gains a budget/profile field carrying an `ExecutionBudget`. Today's `max_items`/`max_pages`/retry budgets remain authoritative for their axes; new axes (wall-clock, network calls) activate only when a budget is supplied. |
| 4 | Dataset append path (`src/runtime/dataset.py::JsonlDataset.append`) | Emits audit/provenance events alongside writes (collector version, run id, policy model version). Raw store stays append-only. |

Provider independence is preserved: `src/policy/` imports stdlib only;
`src/runtime/` may import `src.policy.models` (types) but never TikTok code;
capabilities are generic verbs; TikTok specifics stay inside provider
declarations.

## Data passed into policy evaluation

- actor identity (`provider_name` / harness id)
- requested capability name (+ optional constraint string)
- opaque resource descriptor (URL or path as a plain string — policy does not parse it)
- purpose tag supplied by the caller
- run context: `run_id`, `job_id`, current budget-consumption snapshot from `AcquisitionMetrics`

## Decisions returned

Exactly `PolicyDecision.{ALLOW, DENY, REQUIRE_APPROVAL}` plus (future) a
reason string and matched-rule id for audit records. The enum is the whole
contract — evaluators add metadata, never new verdicts.

## Failure semantics

- Evaluator raises or is absent ⇒ treat as **DENY** (fail closed,
  Constitution §3), emit an audit event, terminate with explicit reason.
- `DENY` ⇒ clean stop via checkpoint commit; reason recorded; no partial
  action lingers.
- `REQUIRE_APPROVAL` ⇒ suspend: state is fully recoverable through the
  existing `CheckpointStore` (atomic tmp+replace, dataset-before-checkpoint
  ordering); resume after approval continues without duplication.
- New termination reason strings are **added, never renamed** — legacy
  checkpoints must keep classifying (`classify_termination` maps unknown to
  `PERMANENT_FAILURE`, so even unrecorded reasons fail visibly).

## Future audit hooks

- Append-only JSONL event stream per run (pattern follows
  `data/manifests/*.jsonl`), written at insertion points 2 and 4.
- Every event carries: timestamp, run/job id, actor, capability, resource,
  decision, matched policy version, budget-before/after.
- Events are emitted even for ALLOW decisions — absence of an event is itself
  a detectable anomaly.

## Future human approval hooks

- Approval store keyed by (actor, capability, resource hash, run_id) with
  mandatory expiry.
- Expired or mismatched approval ⇒ DENY, never silent auto-renewal.
- Approval is recorded as an audit event like any other decision.

## Counterexamples — how this fails if built wrong

1. **Fail-open evaluator.** If evaluation exceptions resolve to ALLOW, a
   crashing evaluator converts every gate into a pass — unbounded network
   work resumes silently. Mitigation: wrap-and-DENY semantics + tests
   asserting the failure path terminates the run.
2. **Budget checked after the action.** A network-call budget verified
   *post-fetch* overshoots by exactly one call every iteration; on an
   infinite-cursor feed that is unbounded work. The item cap already learned
   this lesson (hard truncation mid-page, commit c5cb175) — every new budget
   axis must check *before* dispatching the action.
3. **Approval replay.** An approval stored without resource binding/expiry
   authorizes tomorrow's request against a different target with yesterday's
   yes. Approvals must bind (capability, resource, run) and expire.
4. **Provider leakage.** Hardcoding `"tiktok.scroll"` checks inside the engine
   couples the loop to one platform and quietly exempts every other provider
   from gating. Capabilities belong to provider declarations (insertion
   point 1), evaluated generically at point 2.
