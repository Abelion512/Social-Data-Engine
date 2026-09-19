# Agent Contributor Guide — patching & contributing accurately

> For human + agent contributors. Read this **before** editing `src/`.
> One verification command for everything: `bash scripts/preflight.sh`

## The 60-second map

```
run.sh / src/tiktok_linkedin.py   CLI entry — translates intent, no logic
  └─ src/pipeline/canonical_runner.py   THE one stage implementation
       ├─ src/pipeline/{dedup,quality,thread_builder,identity}.py   tiers
       ├─ src/schema/{canonical,mapper}.py   Observation + DTO mapping
       └─ src/pipeline/legacy.py             re-export SHIM (edit nothing here)
  └─ src/collector.py               browser acquisition (CDP → Camoufox)
       └─ src/browser_selector.py        session attach/launch
  └─ src/runtime/*                  bounded runtime (checkpoint, termination)
  └─ src/pipeline/improve.py        self-healing loop (rule-based planner)
```

**Golden rules (enforced, not suggestions):**

| Rule | Enforced by |
|---|---|
| One pipeline: edit `canonical_runner.py`, never duplicate stages | tests + §6 ledger row |
| No `TODO`/`FIXME`/`XXX`/`HACK` in `src/`, `scripts/` | CI step 6 |
| Every inline `ponytail:` marker needs a `docs/PONYTAIL.md §6` row | CI step 7 |
| New third-party import ⇒ add to `requirements.txt` first | CI step 8 |
| No plaintext credential names in source | CI step 5 |
| `src/runtime/` never imports `src/providers/*` | SDD §3 invariant |
| New non-trivial logic ships with one runnable assert-suite in `tests/` | CI step 4 glob |

## Making a change (the short path)

1. **Locate the real flow** — grep callers of what you touch; fix the root
   cause once in the shared function, not at each call site.
2. **Edit** the module that owns the concern (map above). Simplify per the
   ladder in `docs/PONYTAIL.md`; a deliberate corner-cut gets an inline
   `# ponytail: <ceiling>; upgrade when <trigger>` comment **+** a §6 ledger row.
3. **Add the deterministic check** — `tests/test_<topic>.py`, stdlib-only,
   assert-based; CI discovers it by glob automatically.
4. **Verify**: `bash scripts/preflight.sh` → all 8 gates green.
5. **Live test** (behavior changes only): `agents.md §Live Test Procedure` —
   human-in-the-loop, desktop CDP or visible Camoufox.
6. **Commit** with the why in one line; bump version only after live test.

## Adding a dependency (the only sanctioned way)

Add to `requirements.txt`:
- active line `pkg>=1.0` for core runtime deps, or
- commented `#   pkg>=0.38      → scripts/your_consumer.py` for
  install-on-demand tools (this counts as declared — the gate reads it).

Importing an undeclared package **fails CI** (AST scan of `src/` + `scripts/`,
import-name → dist-name mapping included for the common spellings).

## Optional agent tooling (verified 2026-09-19)

| Tool | What it gives an agent here | Enable (run once, on your machine) |
|---|---|---|
| [graphify](https://github.com/Graphify-Labs/graphify) | Queryable knowledge graph of this codebase (tree-sitter AST, local, deterministic) — `graphify explain "canonical_runner"` instead of grepping | `uv tool install graphifyy && graphify install --project` → type `/graphify .` in your assistant |
| [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | Assistant drives the real Chrome over CDP for live tests (click/scroll/inspect) — complements `src/browser_selector.py`, same `--remote-debugging-port` attach model | `npx chrome-devtools-mcp@latest` (MCP server; point your agent client at it) |
| abelink | **Belum bisa di-review — repo tidak terlihat dari workspace ini.** Owner mempublikasikan `github.com/Abelion512/abelink` sebagai plug; dari Freebuff workspace repo tersebut 404 (bisa jadi *private* — kredensial GitHub App di sini repository-scoped, tidak bisa membaca repo lain), dan pencarian publik juga nihil. Untuk integrasi: buat repo readable (public / invite bot / tempel README-nya ke issue), lalu reviewer agent bisa menilai source sebelum dipakai — jangan import kode yang belum diverifikasi. | — |

Both real tools are assistant-side and optional — no `requirements.txt` entry, no runtime import (dependency gate stays green). Do not commit their outputs (`graphify-out/` stays untracked).

## Where the gates live

| Gate | Script |
|---|---|
| All 8 in one | `scripts/preflight.sh` |
| Ponytail ledger | `scripts/check_ponytail_ledger.py` |
| Dependency gate | `scripts/check_dependencies.py` |
| CI definition | `.github/workflows/ci.yml` (steps 1–8) |
