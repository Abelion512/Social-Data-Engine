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
| Bounded execution runtime | IMPLEMENTED | `src/runtime/` (engine, termination reasons, hard `max_items` cap) |
| Checkpoint / recoverable state | IMPLEMENTED | `src/runtime/checkpoint.py`, `state.py` |
| Provider-independent runtime core | IMPLEMENTED | `docs/RUNTIME.md §B`; runtime imports stdlib + `src.runtime` only |
| Provider adapters (TikTok) | IMPLEMENTED | `src/providers/` behind `src/providers/base.py` |
| Normalization / dedup / quality | IMPLEMENTED | `src/schema/`, `src/pipeline/` (deterministic tests green) |
| Export (manifest / mark / CSV) | IMPLEMENTED | `src/export/` |
| Provenance (metrics + manifests) | PARTIALLY IMPLEMENTED | `src/runtime/metrics.py`, `data/manifests/` |
| Policy / capability gate | PLANNED | nothing exists yet — do not claim enforcement |
| Plugin sandboxing / isolation | PLANNED | nothing exists yet |
| Human approval workflows | PLANNED | nothing exists yet |

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
6. **Provenance required.** `AcquisitionMetrics` + manifests recorded per run.
7. **Deterministic termination.** Every run has a bounded budget and an
   explicit `TerminationReason`.

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
