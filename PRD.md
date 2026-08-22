# Social Data Engine — Product Requirements Document

Version 1.0 · 2026-08-22 · Derived from `PRODUCT.md`; elaborated into testable
form in `SRS.md` (every row below maps to FR/NFR ids there).

Priority semantics:
- **MUST** — product is not honest without it; blocks phase exit.
- **SHOULD** — expected in the referenced phase window; deferral must be recorded.
- **LATER** — real requirement, explicitly not now; revisit trigger defined.
- **OUT** — never, absent a new decision record (`DECISIONS.md` amendment).

---

## 1. Actor

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Every actor exposes validated identity: `actor_id`, `actor_version`, `provider`; missing/invalid identity fails closed. | FR-ACT-001 |
| MUST | Two versions of one actor produce distinguishable run metadata (summaries, checkpoints, manifests). | FR-ACT-004 |
| MUST | Capability declaration is structural: zero capabilities allowed and labeled; duplicates rejected. | FR-ACT-002/003 |
| SHOULD | Actor metadata beyond identity (description, homepage) carried in provenance. | FR-ACT-005 |
| LATER | Actor registry/discovery for third-party actors. Revisit when ≥3 real actors exist. | — |
| OUT | Running untrusted third-party actor code (no sandbox exists or is planned). | — |

## 2. Harness

| Pri | Requirement | SRS |
|---|---|---|
| MUST | `RunInput` validates fail-closed, serializes round-trip, binds to actor identity before execution. | FR-HAR-001/002 |
| MUST | Harness delegates to an unmodified runtime; summary type identical via harness or direct runtime path. | FR-HAR-003 |
| MUST | Run lifecycle states exist and are derived deterministically from outcomes. | FR-RUN-001 |
| SHOULD | Dry-run mode: validate declarations + input, execute nothing, report what would run. | FR-HAR-004 |
| LATER | Multi-actor orchestration facade. | — |
| OUT | Harness as scheduler or long-lived supervisor. | — |

## 3. Acquisition

| Pri | Requirement | SRS |
|---|---|---|
| MUST | TikTok photo + video URL forms parse to canonical ids. | FR-ACQ-001 |
| MUST | Threaded replies expanded recursively with parent linkage preserved through dedup. | FR-ACQ-002 |
| MUST | Partial captures are resumable by pinned `job_id` without duplication; corrupt checkpoints fail loudly. | FR-ACQ-003, FR-DAT-002 |
| MUST | Coverage (captured/reported) computed and reported per run. | FR-ACQ-004 |
| SHOULD | Wall-clock and network-call budget axes enforced (today only item/page/retry budgets bind). | NFR-003 gap |
| LATER | Second real provider end-to-end. Revisit trigger: evaluator v0 merged. | — |
| OUT | Anti-detection arms race as a capability; captcha-solving as an advertised feature. | — |

## 4. Processing

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Normalization keeps `text_raw` alongside `text_normalized`. | FR-PRC-001 |
| MUST | Dedup preserves thread structure (parent survives) and reports dup_rate. | FR-PRC-002 |
| MUST | Quality scoring gates curated output at declared threshold (0.35 default). | FR-PRC-003 |
| SHOULD | Gate thresholds configurable per run via `RunInput.config` axes only. | FR-PREF-001 |
| LATER | Enrichment beyond images/media. | — |
| OUT | ML/model-based processing in the core pipeline. | — |

## 5. Dataset

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Append-only tiered JSONL (`raw`/`curated`/`normalized`/`enriched`) with raw↔curated id traceability. | FR-DAT-001, FR-PROV-001 |
| MUST | Legacy checkpoint/dataset fields stay byte-compatible (additive evolution only). | NFR-009 |
| MUST | Dataset location discoverable from the run summary without filesystem guessing. | FR-DAT-003 |
| SHOULD | Dataset manifest/index per run (files, counts, schema version). | FR-PROV-002 |
| LATER | Query/export interfaces beyond current experimental CSV export. | — |
| OUT | Hosted database service; multi-writer concurrency guarantees. | — |

## 6. Run

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Every run terminates finitely under pre-declared budgets; failed runs still emit summaries plus partial artifacts. | FR-RUN-002/003 |
| MUST | Resume requires explicit job identity; ambiguous resume fails closed. | FR-RUN-004 |
| SHOULD | Run inspection command (list/summarize past runs from checkpoints+manifests). | FR-TRANS-004 |
| LATER | Cross-run comparison reports. | — |
| OUT | Distributed/multi-host run coordination. | — |

## 7. Loop

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Internal loops bounded (`max_iter` ≤ sane cap), per-iteration metrics recorded, always terminating. | FR-LOOP-001/002 |
| MUST | No loop may alter the declared intent (target, actor identity); only parameters within it. | FR-LOOP-003 |
| SHOULD | Loop telemetry includes measured deltas per iteration, not just final state. | FR-SI-003 |
| OUT | Autonomous loop engines, self-set goals, unbounded recursion. | — |

## 8. Self-improvement

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Planner is deterministic and rule-based (no LLM in the planning loop). | FR-SI-002 |
| MUST | Every planned action + resulting metrics appended to an append-only manifest with timestamps. | FR-SI-001 |
| MUST | Improvement claims require recorded before/after metric delta; memory writes alone never count. | FR-SI-003 |
| SHOULD | Lesson schema v1: observation → action → measured outcome → accepted/rejected flag. | FR-SI-004 |
| LATER | Cross-run strategy learning; preference-derived adjustments. Revisit after Phase 4 evidence. | — |
| OUT | Claims of intelligence gains; model training loops. | — |

## 9. Provenance

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Run manifests record actor id/version, declared capabilities, budgets, outcome, lifecycle, policy-model version. | FR-PROV-002 |
| MUST | Manifests are append-only and machine-verifiable (verification command or library function). | FR-PROV-003 |
| SHOULD | Manifest schema carries its own version constant; writers refuse unknown future schemas. | FR-PROV-004 |
| LATER | Cryptographic signing of manifests. | — |
| OUT | Tamper-proof distributed ledger fantasies. | — |

## 10. Preferences

| Pri | Requirement | SRS |
|---|---|---|
| MUST (v1) | Preferences enter exclusively as explicit `RunInput.config` / CLI flags; nothing is inferred silently. | FR-PREF-001 |
| SHOULD | Precedence documented: defaults < config < explicit options; conflicts fail closed. | FR-HAR-002 note |
| LATER | Preference store with audit trail, owned outside the improve subsystem. | — |
| OUT | Behavioral profiling of operators or data subjects. | — |

## 11. Policy

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Single capability vocabulary source (`src/policy/models.py`); declarations reference it, not ad-hoc strings. | FR-POL-001 |
| MUST | Evaluator v0: deterministic mapping `(actor, capability, context) → ALLOW/DENY/REQUIRE_APPROVAL`, deny-by-default on uncertainty. | FR-POL-002 |
| MUST | Denials surface in run summaries/termination reasons (`policy_denied`) with reason codes; no silent degradation. | FR-POL-003 |
| MUST | No **sanctioned entry point** (harness; CLI after Phase 3) reaches provider actions without passing the gate. Direct `AcquisitionRuntime` use is documented trusted-internal (same trust level as actor code, FR-SEC-001) and is not a sanctioned external path — a library cannot enforce more without sandboxing. | FR-POL-004 |
| SHOULD | Named profiles (permissive/restricted) as versioned configuration. | FR-POL-005 |
| LATER | `REQUIRE_APPROVAL` workflow + approval store (Constitution §10). | — |
| OUT | Runtime policy-authoring UI; per-request dynamic policy negotiation. | — |

## 12. Safety

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Human-in-the-loop for interactive login/captcha; agent never takes over authentication (workflow gate, `agents.md`). | FR-SAF-002 |
| MUST | Destructive operations require explicit flags; no auto-recovery that discards state. | FR-SAF-003 |
| MUST | Live-test-before-merge remains the gate for collection-behavior changes. | FR-SAF-001 |
| SHOULD | Dry-run defaults everywhere configuration can cause external effects. | NFR-006 |
| LATER | Runtime override verbs over executing actions. | — |
| OUT | Fully unsupervised operation modes. | — |

## 13. Security

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Zero plaintext credentials in code/config/contracts; cookie/profile-based auth only. | FR-SEC-002 |
| MUST | Contract models reject/sanitize secret-bearing context (fail-closed parsing). | FR-SEC-003 |
| MUST | In-process host-trust execution of actors is a **documented limitation**, surfaced wherever actors are documented. | FR-SEC-001 |
| SHOULD | Mechanical secret scanner over contract payloads (`RunInput.payload/config`, `CapabilityRequest.metadata`). | FR-SEC-004 |
| LATER | Per-plugin capability grants. | — |
| OUT | Sandboxing/process isolation of actor code (deferred by explicit decision; tracked as limitation). | — |

## 14. Transparency

| Pri | Requirement | SRS |
|---|---|---|
| MUST | Every run emits a human-readable summary incl. counts, coverage, quality mean, durations, termination reason, lifecycle. | FR-TRANS-001/002 |
| MUST | Constitution status labels stay evidence-based; docs may not outrun tests. | NFR-010 |
| SHOULD | Agent action traces persisted (BrowserAgent trace logs formalized). | FR-TRANS-003 |
| OUT | Marketing claims of any kind in technical docs. | — |

## 15. External agent integration

| Pri | Requirement | SRS |
|---|---|---|
| MUST | CLI and import entry points behave identically; stable exit-code taxonomy. | FR-INT-001/003 |
| MUST | `RunInput`/`RunSummary` schemas carry version constants; readers reject unknown major versions. | FR-INT-002 |
| SHOULD | One worked example integrating a generic agent loop (pseudo-agent, framework-neutral). | FR-INT-004 |
| LATER | Published schema artifacts + packaging (`pyproject.toml`) so agents can install SDE. | D-012 |
| OUT | Hosted SaaS; exclusive integrations with named agent products. | — |
