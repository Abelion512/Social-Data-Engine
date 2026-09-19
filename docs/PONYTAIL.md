# Ponytail — the laziness rule this repo runs on

Upstream: [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) (MIT).
"The best code is the code you never wrote." Lazy means *efficient*, not careless.

`agents.md` rule 5 is this file in one line; this is the long form plus this
repo's working agreement around it.

---

## 1. The ladder

Stop at the **first rung that holds** — but only *after* you understand the
problem: read the code the change touches and trace the real flow first. A small
diff in the wrong place is not lazy, it's a second bug.

1. Does this need to exist at all? → no: skip it (YAGNI)
2. Already in this codebase? → reuse it, don't rewrite it
3. Stdlib does it? → use it
4. Native platform feature covers it? → use it
5. Already-installed dependency? → use it
6. One line? → one line
7. Only then: the minimum that works

Bug report = fix the **root cause**: grep every caller of the function you touch
and fix it once, there, rather than guarding each call site.

**Never on the chopping block** (these are in `agents.md` and
`docs/ENGINEERING_CONSTITUTION.md` too): input validation at trust boundaries, error
handling that prevents data loss, security, provenance, and anything the user
explicitly asked for. Lazy code without its check is unfinished: non-trivial
logic leaves **one runnable check** behind (assert-based self-check or one small
suite, no framework).

## 2. Review vocabulary

Audit and review output is one line per finding, ranked biggest cut first:

| Tag | Means |
|---|---|
| `delete:` | dead code, unused flexibility, speculative feature. Replacement: nothing. |
| `stdlib:` | hand-rolled thing the standard library ships. Name the function. |
| `native:` | code doing what the platform already does. |
| `yagni:` | abstraction with one implementation, config nobody sets, layer with one caller. |
| `shrink:` | same logic, fewer lines. Show the shorter form. |

Format: `<tag> <what to cut>. <replacement>. [path]`, then
`net: -<N> lines, -<M> deps possible.`

## 3. `ponytail:` — mark the ceiling, don't hide it

A deliberate simplification that cuts a real corner gets an inline comment
naming the **ceiling** and the **upgrade path**:

```python
# ponytail: O(n²) within one parent bucket; upgrade to MinHash when >50k uniform records
```

A ceiling with no marker is indistinguishable from a bug. Ledger of live
markers: §6.

## 4. How to run it in this repo

| Command | Where | Output |
|---|---|---|
| `/ponytail-review` | on the diff, before a PR | ranked findings, fix or ledger them |
| `/ponytail-audit` | whole repo | `docs/PLANNED/sessions/<date>_*.md` |
| `/ponytail-debt` | harvest `ponytail:` markers | §6 of this file |

Instruction-only adapters (Claude Code, Codex, Cursor, Copilot, Gemini, …) read
`agents.md`; nothing has to be installed for the ladder to apply here. For
always-on injection + the commands:

```bash
/plugin marketplace add DietrichGebert/ponytail   # Claude Code
/plugin install ponytail@ponytail
codex plugin marketplace add DietrichGebert/ponytail && codex plugin add ponytail@ponytail
```

**Mechanical gate** (`.github/workflows/ci.yml` step 6): a deferred-work marker
(`TODO|FIXME|XXX|HACK`) in `src/` or `scripts/` fails CI. Finish it, or record a
`ponytail:` ceiling + an entry in §6. A gate that cannot fail is decoration.
Two more gates (added 2026-09-19): **CI step 7** enforces that every inline
`ponytail:` marker has a §6 row (`scripts/check_ponytail_ledger.py`), and
**CI step 8** blocks any third-party import that is not declared in
`requirements.txt` (`scripts/check_dependencies.py` — AST-scan, commented
optionals count as declared). One command for all eight gates:
`bash scripts/preflight.sh`.

## 5. Boundary

This audit covers **over-engineering only**. Correctness bugs, security holes and
performance regressions are a normal review pass — they are not "stuff to
delete". Equally, code the project explicitly requested (see `docs/SRS.md`,
`docs/PRD.md`, `docs/ROADMAP.md`, `docs/ENGINEERING_CONSTITUTION.md`) is not speculative: it is
scoped and labelled (`docs/CURRENT-STATE.md` §1/§1b/§3) rather than thrown away.

## 6. Debt ledger

Harvested `ponytail:` markers + findings deliberately deferred. "Later" only
counts if it is written down with a trigger.

| Ceiling / deferral | Where | Revisit when |
|---|---|---|
| Near-dup tier prunes on a size window; same-length buckets stay O(n²), MinHash/LSH not built | `src/pipeline/dedup.py` | >50 000 uniform-length records in one parent bucket |
| Legacy near-dup check compares only within a 16-bit simhash bucket (recall ceiling) | `src/pipeline/legacy.py::stage_dedup` | when near-dup recall is measurably low, or when the modular dedup tier takes over |
| Thread building is a single O(n) pass; deeper-than-2-level nesting untested | `src/pipeline/thread_builder.py` | a provider with >2 reply levels appears |
| Coverage denominator = DOM-rendered comment count (guest view, rounded) | `src/collector.py::_probe_reported_count` | when the API `total`/`has_more` is wired into coverage math |
| Two pipeline implementations: ~~`legacy.py` (runs in the CLI) vs the modular `stages/dedup/quality`~~ **RESOLVED 2026-09-19** — `canonical_runner.py` is now the single stage implementation (modular tiers wired into the CLI surface); `legacy.py` is a re-export shim | `src/pipeline/canonical_runner.py` | closed; reopen only if a second stage implementation reappears |
| Enrich-stage quality annotation uses the modular scorer on flat record dicts (flex dict/Observation entry in `compute_quality_score`) | `src/pipeline/quality.py` | if the enrich stage migrates fully to canonical Observations end-to-end, drop the dict branch |
| Ponytail-ledger gate matches ledger rows generously (module path, filename, or dotted name in any §6 cell) — no structured marker IDs | `scripts/check_ponytail_ledger.py` | if false positives appear, switch to a `ponytail-id:` convention with exact ledger references |
| Cross-provider identity matching deleted (no caller, no test — see §7) | `src/pipeline/identity.py` | with the second real provider (Phase 5), where a cross-provider pair can actually exist |
| Content id parser is path-based, no host allow-list (S-G7 egress scoping) | `src/tiktok_schema.py::parse_content_id` | Phase 3→4; changes navigation → live gate (threat model §2.14) |

## 7. What was cut, and what was not

The 2026-09-19 pass (record: `docs/PLANNED/sessions/2026-09-19_ponytail-audit.md`)
removed dead code, dead imports and hand-maintained boilerplate, and turned the
CI "ponytail gate" from an `echo` into a real check.

It deliberately did **not** remove `src/runtime/`, `src/harness/`, `src/policy/`,
`src/providers/` or `src/schema/`: those layers are requested by `docs/SRS.md` /
`docs/PRD.md`, carry their own deterministic suites, and are labelled honestly in
`docs/CURRENT-STATE.md`. Deleting requested, tested architecture is not laziness — it
is destroying the spec to make a diff look small.
