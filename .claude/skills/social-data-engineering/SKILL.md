---
name: social-data-engineering
description: >
  Engineering skill for Social Data Engine. Use this skill whenever modifying,
  reviewing, testing, refactoring, or architecting the repository. Enforces
  evidence-first development, adversarial review, checkpoint-first recovery,
  provider/runtime separation, reproducibility, data integrity, security,
  safety, transparency, and minimal-change engineering.
---

# Social Data Engine Engineering Skill

## Mission

Treat Social Data Engine as a long-lived, self-hosted, open-source,
Apify-like social-data execution platform.

The project is NOT merely a scraper.

Long-term architecture:

```text
Harness / Actor
      ↓
Policy / Capability Gate
      ↓
Bounded Execution Runtime
      ↓
Provider Acquisition
      ↓
Raw Dataset
      ↓
Normalization
      ↓
Deduplication
      ↓
Quality
      ↓
Provenance
      ↓
Research / ML / Export
```

## Component status (evidence-first — do not overstate)

| Component | Status | Where |
|---|---|---|
| Bounded execution runtime | PARTIALLY IMPLEMENTED | `src/runtime/` — hard `max_items` cap, page cap, retry/stall budgets; NO wall-clock or network-call budget; legacy collector scripts and harness agents are not uniformly bounded |
| Checkpoint / recoverable state | PARTIALLY IMPLEMENTED | `src/runtime/checkpoint.py` — atomic writes, durability ordering, resume reconciliation, and fail-closed corrupt-checkpoint handling (explicit terminal `checkpoint_corrupt`, never silent fresh-run) proven for `AcquisitionRuntime` only; legacy pipeline persistence is separate and ad-hoc |
| Provider-independent runtime core | IMPLEMENTED | `docs/RUNTIME.md §B`; runtime imports stdlib + `src.runtime` only |
| Provider adapters (TikTok) | IMPLEMENTED | `src/providers/` behind `src/providers/base.py` |
| Harness / actor contract | PARTIALLY IMPLEMENTED | `src/runtime/harness.py` — `ActorHarness` validates identity + DECLARED capabilities (structural only, recorded into provenance), `RunInput` fail-closed + serializable, `RunLifecycle` states; NO evaluation/grant/deny, no sandbox; production TikTok page-source wiring pending |
| Normalization / dedup / quality | IMPLEMENTED | `src/schema/`, `src/pipeline/` (deterministic tests green) |
| Export (manifest / mark / CSV) | PARTIALLY IMPLEMENTED | `src/export/` modules exist and are wired into scripts; no dedicated deterministic tests prove their output |
| Provenance (metrics + manifests) | PARTIALLY IMPLEMENTED | `Provenance`/`Confidence` schema classes declared; `AcquisitionMetrics` persisted in checkpoints/`RunSummary`; manifests are written only by the legacy improve loop — NOT universally by every acquisition path |
| Policy / capability gate | PLANNED | contract models exist (`src/policy/`: `Capability`, `CapabilityRequest`, `PolicyDecision`, `ExecutionBudget`) but NOTHING evaluates or enforces them — no evaluator, no sandbox, no approvals |
| Plugin sandboxing / isolation | PLANNED | nothing exists yet — harness tools run with full process privileges |
| Runtime human override (stop/approve/deny/escalate) | PLANNED | nothing exists in the executing run; developer-process human-in-the-loop (live-test gate, manual login/captcha) is workflow control, NOT runtime override |

When documenting or reviewing, label claims `IMPLEMENTED`,
`PARTIALLY IMPLEMENTED`, `DOCUMENTED ONLY`, or `PLANNED`. Cite the test or
doc that proves each claim. Never describe a PLANNED control as active.

## Operating rules (normative source: `agents.md`)

1. **Live-test before merge.** Deterministic tests passing is necessary but
   not sufficient; behavior-affecting changes need a live run (human-in-the-loop).
2. **Deterministic planner.** No LLM inside `src/pipeline/improve.py` planning.
3. **Dry-run by default.** `scripts/version_bump.py` writes only with
   explicit `--commit --push`.
4. **Zero plaintext credentials.** Cookie-based auth only; never
   `LINKEDIN_PASSWORD`/`USERNAME` in code.
5. **Ponytail ladder.** YAGNI / reuse / stdlib / minimal — never cut
   validation, error handling, security, or provenance.
6. **Provenance-aware.** Preserve and propagate provenance wherever the
   current pipeline supports it (`AcquisitionMetrics`, run/job ids,
   manifests where written). Never fabricate missing provenance; missing
   provenance must remain visible as a known limitation until universal
   enforcement exists (Constitution §8 — PARTIALLY IMPLEMENTED).
7. **Deterministic termination.** `AcquisitionRuntime` runs end with an
   explicit `TerminationReason` + `Outcome` classification under
   item/page/retry budgets. Other entry points (legacy collector scripts,
   harness agent) are not uniformly bounded or classified yet.
8. **Capability declarations are vocabulary.** `ActorHarness` validates
   declaration STRUCTURE only and records it into provenance; it never
   grants, denies, or evaluates. Do not describe declared capabilities as
   enforced or granted.

## Working practices

- **Evidence-first.** Verify a claim against code/tests before writing it
  into docs, PRs, or commit messages.
- **Adversarial review.** Before finishing, attack your own change: find
  counterexamples, failure semantics, and regressions; document findings.
- **Checkpoint-first recovery.** Long-running work must be resumable from
  checkpoint; writes follow the atomic temp-file + rename pattern.
- **Provider/runtime separation.** `src/runtime/` must never import a
  provider; providers implement the `src/providers/base.py` contract.
- **Security & privacy floor.** Secrets never in datasets or logs; the real
  scraped corpus stays out of Git (ignored `data/` outputs; synthetic
  fixtures live in `data/samples/`).
- **Reproducibility.** Pin dependencies in `requirements.txt`; keep unit
  tests deterministic; record metrics/manifests per run.
- **Minimal-change engineering.** Fewest files and lines that satisfy the
  request; edit existing files before creating new ones; no speculative
  abstractions.

## Definition of done (pre-merge gates)

- `python -m py_compile src/*.py src/*/*.py scripts/*.py tests/*.py`
- `python tests/run_dedup_quality_tests.py` and
  `python tests/test_self_improvement.py` green
- `bash -n run.sh` (and `zsh -n run.sh` where zsh exists)
- `grep -rn "LINKEDIN_PASSWORD\|LINKEDIN_USERNAME" src/` → 0 hits
- Existing suites untouched: do not weaken tests to make them pass
- Live test for behavior-affecting changes (see `agents.md`)
