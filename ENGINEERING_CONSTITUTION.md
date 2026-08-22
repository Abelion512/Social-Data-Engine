# Engineering Constitution

Non-negotiable principles for all future runtime, harness, actor, provider,
and loop-engine development in Social Data Engine.

Status labels are evidence-based. **Do not upgrade a label without tests or
code that proves the stronger guarantee.**

- `IMPLEMENTED` — enforced by code today, covered by deterministic tests.
- `PARTIALLY IMPLEMENTED` — enforced in some paths/scopes; gaps listed.
- `DOCUMENTED ONLY` — stated here; nothing in the codebase enforces it.
- `PLANNED` — designed (see `docs/architecture/POLICY-ARCHITECTURE.md`),
  not built.

---

## 1. Least privilege — DOCUMENTED ONLY
**Means:** every component gets the minimum capabilities needed, scoped in
time and target.
**Does NOT mean:** convenience-wide-open access with review after the fact.
**Future enforcer:** policy evaluator gating each action (`src/policy/`
models exist as vocabulary only; nothing evaluates them today).

## 2. Safe defaults — PARTIALLY IMPLEMENTED
**Means:** unset configuration resolves to the safest behavior (deny,
dry-run, visible, capped).
**Does NOT mean:** every default is already safe.
**Enforced today:** `scripts/version_bump.py` is dry-run unless
`--commit --push`; collection runs are capped by `RunOptions` defaults;
credentials come from cookies, never plaintext.
**Not yet safe by default:** any script can open network connections and
write anywhere the process can write.
**Future enforcer:** deny-by-default policy profile.

## 3. Fail closed — PARTIALLY IMPLEMENTED
**Means:** on uncertainty (error, unknown state, missing evaluator), stop or
deny; never guess toward "allow".
**Does NOT mean:** crashing without cleanup — failure must still checkpoint.
**Enforced today:** unknown/unclassified termination reasons classify as
`PERMANENT_FAILURE` (`src/runtime/termination.py`); a corrupt/unreadable
checkpoint on resume produces an EXPLICIT terminal recovery failure
(`checkpoint_corrupt`) — it MUST NOT be silently treated as a fresh run;
low-quality records are gated out.
**Recovery rule:** resume never guesses. Discarding, moving, or rebuilding a
corrupt checkpoint is an explicit human recovery decision — any destructive
or duplicate-prone recovery requires that decision, never an automatic
fallback.
**Not yet enforced:** there is no policy layer whose failure must deny.
**Future enforcer:** evaluator exceptions resolve to `DENY`.

## 4. Bounded execution — PARTIALLY IMPLEMENTED
**Means:** every run has finite, pre-declared limits on work.
**Does NOT mean:** limits exist on every entry point today.
**Enforced today:** `AcquisitionRuntime` enforces a HARD unique-item cap,
page cap, and retry/stall budgets; `max_items` truncates even mid-page
overshoot.
**Gaps:** no wall-clock or network-call budget; legacy collector scripts and
harness agents are not uniformly bounded.
**Future enforcer:** `ExecutionBudget` consumed by the execution loop.

## 5. Explicit capabilities — DOCUMENTED ONLY
**Means:** components declare what they need (network, filesystem, process,
browser); undeclared actions are unavailable.
**Does NOT mean:** renaming permissions after the fact or implicit grants by
import.
**Exists:** vocabulary (`src/policy/models.py`: `Capability`,
`CapabilityRequest`) + structural DECLARATION at the actor boundary
(`AcquisitionActor.capabilities()`, structurally validated by `ActorHarness`
and recorded into run provenance). Declarations are still never required,
ever granted, and never checked against actions — nothing enforces them.
**Future enforcer:** capability declaration at actor/harness registration +
evaluation before each action.

## 6. Deterministic termination — PARTIALLY IMPLEMENTED
**Means:** every run ends through a known reason with a classified outcome —
never an infinite loop, never a silent hang.
**Does NOT mean:** termination reasons are stable API across refactors;
checkpoint strings stay byte-compatible with legacy values.
**Enforced today:** every `AcquisitionRuntime` exit path sets a
`TerminationReason`, classified into an `Outcome`.
**Future enforcer:** extended reasons (`policy_denied`,
`approval_required`) added without renaming legacy ones.

## 7. Auditability — PARTIALLY IMPLEMENTED
**Means:** significant actions and decisions leave an inspectable trace.
**Does NOT mean:** tamper-proof, cryptographically signed logging exists.
**Enforced today:** `AcquisitionMetrics` + pagination state are committed
into checkpoints and summarized in `RunSummary`; pipeline manifests record
self-improvement iterations.
**Gap:** no append-only audit event stream for individual actions/decisions.
**Future enforcer:** audit hooks at the policy decision points (architecture
doc, insertion points 2 and 4).

## 8. Provenance — PARTIALLY IMPLEMENTED
**Means:** data carries where it came from, who processed it, when, how.
**Does NOT mean:** provenance is complete on every stored record today.
**Enforced today:** `Provenance`/`Confidence` schema classes exist and are
declared required at every canonical level; runs persist provider, job/run
ids, timestamps.
**Gap:** manifest/provenance writing lives in the legacy self-improvement
loop, not universally in acquisition writers.
**Future enforcer:** provenance emitted automatically at dataset append.

## 9. Reproducibility — PARTIALLY IMPLEMENTED
**Means:** same inputs + same code + same policy → same observable result;
the environment is pinned enough to recreate it.
**Does NOT mean:** bit-identical live scrapes (live sources change); it means
deterministic processing given captured inputs.
**Enforced today:** deterministic unit suites (dedup/quality/runtime),
synthetic fixtures in `data/samples/`, pure rule-based planner.
**Gap:** `requirements.txt` uses lower-bound pins only; no lockfile; policy
versions are not yet recorded with outputs.
**Future enforcer:** locked environments + policy version stamping.

## 10. Human override — PLANNED
**Means:** runtime-level verbs — **stop / approve / deny / escalate** —
exercisable by a human over an EXECUTING autonomous action.
**Does NOT mean:** developer-process human-in-the-loop counts as override.
Live-test-before-merge approval, manual login/captcha fallback, and code
review are workflow controls outside the executing run; they do not give a
human authority over an in-flight action. The captcha-solving/stealth
automation is likewise not oversight — it is exactly the class of capability
that must sit behind approval once the gate exists
(see `policies/ACCEPTABLE-USE.md`).
**Status:** nothing in the codebase exposes runtime override verbs today.
**Future enforcer:** `REQUIRE_APPROVAL` decision path + approval store
(stop/approve/deny/escalate) wired into the execution loop
(`docs/architecture/POLICY-ARCHITECTURE.md`, approval hooks).

## 11. Versioned policy — DOCUMENTED ONLY
**Means:** policy rules are versioned; every decision records which policy
version produced it.
**Does NOT mean:** a YAML/JSON policy format exists yet.
**Exists:** `POLICY_MODEL_VERSION` constant stamped into serialized policy
models.
**Future enforcer:** immutable policy profiles referenced by run configs and
stamped into audit events.

## 12. Separation of raw observations and derived inference — IMPLEMENTED (storage layout)
**Means:** captured observations and later-derived annotations never overwrite
or masquerade as each other.
**Does NOT mean:** every writer enforces the separation mechanically yet.
**Enforced today:** distinct output tiers (`data/raw`, `curated`,
`normalized`, `enriched`); `Content.text_raw` coexists with
`text_normalized`; `Annotation` carries its own model + version.
**Future enforcer:** writer-level refusal to mix tiers.

## 13. Recoverable checkpoints — PARTIALLY IMPLEMENTED
**Means:** interrupted work resumes without duplication or corruption.
**Does NOT mean:** all pipelines are checkpoint-based today.
**Enforced today:** atomic tmp+replace checkpoints, durability ordering
(dataset write BEFORE checkpoint commit), resume reconciles the dataset by
replaying ids, and a corrupt checkpoint fails loudly as an explicit terminal
recovery failure instead of masquerading as a fresh run — all scoped to
`AcquisitionRuntime`.
**Future enforcer:** the same primitives generalized to loop engines and
harnesses.

## 14. Minimal privilege for plugins / harnesses — DOCUMENTED ONLY
**Means:** third-party actors/harnesses get individually granted, sandboxed
capabilities — never host-level trust.
**Does NOT mean:** today's harness tools are constrained; they run with full
process privileges.
**Future enforcer:** per-plugin capability grants + sandbox boundary
(explicitly out of scope until designed).

## 15. No silent policy bypasses — DOCUMENTED ONLY
**Means:** bypassing policy is impossible by construction, and attempted
bypasses are loud, audited failures.
**Does NOT mean:** documentation alone prevents bypass.
**Precedent:** CI grep gates (plaintext credentials) show the repo accepts
mechanical enforcement.
**Future enforcer:** fail-closed evaluation + audit of denied/bypass
attempts; no code path reaches an action without a decision.

---

## Amendment rule

Changing a principle requires a PR that updates this file AND the affected
`policies/*.md` documents together, with status labels re-justified against
tests. Labels move forward only with proof, never to make a document look
better.
