# Safety Policy (engineering policy — not a legal contract)

Safety = runs do bounded, intended work, stop deterministically, and never
escalate on their own.

## CURRENTLY ENFORCED

- **Hard unique-item cap.** `AcquisitionRuntime` never persists more than
  `RunOptions.max_items` unique items, even when a single page overshoots
  (mid-page truncation, dupes excluded from the cap).
- **Bounded pagination + retries.** `max_pages`, `max_retries`,
  `max_empty_retries`, `max_stalls`, `max_parse_retries` all terminate the run
  through classified reasons (`src/runtime/termination.py`).
- **Auth-block stops the run.** `auth_blocked` terminates instead of hammering
  a challenge endpoint.
- **Deterministic termination.** Every `AcquisitionRuntime` exit path records
  an explicit `TerminationReason` classified into an `Outcome`.

## CURRENTLY PARTIALLY ENFORCED

- **Safe defaults** — collection caps default on; dry-run defaults exist for
  versioning. But browser automation itself runs with full process privileges.
- **Explicit termination visibility** — reasons are recorded in checkpoints
  and `RunSummary`; there is no external "stop" switch a human can flip
  mid-run.

## PLANNED

- **Wall-clock and network-call budgets** (`ExecutionBudget.max_runtime_seconds`,
  `max_network_calls`) consumed by the loop — vocabulary exists in
  `src/policy/models.py`, nothing consumes them yet.
- **Human override** — runtime verbs **stop / approve / deny / escalate**
  over an executing autonomous action; `REQUIRE_APPROVAL` routes dangerous
  future capabilities (process execution, new external endpoints,
  evasion-class behavior) to it. Developer-process human-in-the-loop
  (live-test gate, manual login/captcha fallback) is NOT this mechanism.
- **Kill switch** — operator-visible, run-scoped stop signal honored between
  actions.
- **Loop-engine bounds** — the future autonomous loop must inherit the same
  budget + termination discipline as acquisition runs.

## Explicitly out of scope

Autonomous loop execution, sandboxing, and approval workflows are NOT
implemented. Nothing here claims they exist.
