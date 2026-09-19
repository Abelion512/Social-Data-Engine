# Session Log — 2026-09-19 · security audit + hardening

## Goal
User request: *"Find vulnerabilities · make improvement suggestions and fix ·
refactor · write unit tests · update documentation."* Method: audit the entry
points the previous S-G2 sweep had NOT covered (it covered the runtime, loop and
manifest builders), reproduce each finding before touching it, fix with a
deterministic guard, and record residuals honestly.

## Audit method (no guessing)
| Sweep | Result |
|---|---|
| Dangerous primitives (`shell=True`, `os.system`, `eval/exec`, `pickle`, `yaml.load`, `__import__`) | none found |
| Identifier → path interpolation (`f"{id}"` inside `Path(...)`) | **4 clusters found** (exporters, legacy CLI, consumer, manifest writer) |
| Untrusted data → `subprocess` argv | **found** (LinkedIn consumer handles/names) |
| Declared limits vs enforced limits | **found dead config** (`LINKEDIN_LIMITS` never consulted) |
| Output file modes for PII/credentials | cookies already fixed (previous session); reports + state **not** |
| Child-process environment | full `os.environ` inherited (router/LLM keys) |
| CDP/network surface | localhost-only, hard-coded ports — clean |
| Credential grep gate | 0 hits |

## Findings → fixes
1. **P1 TM-26 — arbitrary read + write via a CLI identifier.** `export_video`
   interpolated the id into a curated-file read and a `data/mark/<id>.json`
   write; the legacy stage CLI did the same across six data tiers;
   `write_manifest` trusted caller-supplied ids. → all validate with the existing
   S-G2 `require_slug_identifier` and resolve through the new `rooted_file()`.
2. **P2 TM-27 — argument injection into `linkedin-cli`.** Handles/names from
   scraped data sat directly after the subcommand in argv, so `--json` or `-o`
   would be parsed as flags. → `safe_handle()` charset (leading alphanumeric)
   plus leading-`-` query refusal; the wrappers now refuse *without spawning*.
3. **P2 TM-28 — unenforced action budget + PII/secret exposure.** The advertised
   20-connections/day cap and pacing were dead config (`daily_conn_count()` was
   never called); CSV reports and state JSON were world-readable; the child
   process inherited every env var. → cap enforced per send (reported when hit),
   pacing `max(configured, 1000 ms)`, `write_private_text` (0600, BOM kept for
   Excel), child env filtered of `KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE`.
4. **P3 — benchmark id derivation.** `url.rsplit("/")` produced junk ids for
   `?query` URLs and photo links. → canonical parser.

## Refactor (removing duplication, not adding abstraction)
- **Four copies** of the TikTok content-id regex (collector ×2, provider actor,
  CLI, probe script) collapsed into `src/tiktok_schema.parse_content_id` /
  `try_parse_content_id`; `providers.tiktok_actor.parse_tiktok_video_id` now
  delegates (identical semantics, identical `ValueError` message).
- New `src.runtime.context.rooted_file()` sits beside the existing
  `require_slug_identifier` / `resolve_rooted`, so writers read
  `rooted_file(root, f"{vid}.json", "label")` instead of hand-built paths.
- Removed now-unused imports (`re` in two modules) and dead config usage
  (`daily_conn_count()` is consulted again).

## Unit tests
`tests/test_input_validation.py` (15): refusal per entry point for 7 hostile ids,
"no file written" assertion, valid-id behaviour preserved, parser single-source
equivalence (incl. the `?query` URL the old rsplit broke and the documented
host-agnostic property), collector refusing non-TikTok URLs without a browser,
handle charset, "no subprocess spawned for a refused handle", daily cap blocking,
pacing floor + remaining-budget arithmetic, invalid match handle refused, env
secret stripping, and 0600 modes (+ BOM) for state and reports.

Full matrix: **19 suites / 202 assertions green**; `py_compile`, CI symbol check,
`bash -n run.sh`, credential grep 0 hits, 0 TODO/FIXME, AST undefined-global scan 0.

## Live-test gate (agents.md)
NOT run — headless sandbox (no display/CDP, no Camoufox binary), the constraint
recorded in VERIFICATION §7/§9/§11/§12. Justification: no collection behaviour
changed (the URL→id parse keeps the same regex and error text, proven by test and
by the collector's own refusal path); the fixes reject hostile input earlier,
tighten file modes, and bound an outbound LinkedIn budget. The gate stays
mandatory before any merge that changes acquisition behaviour.

## Deliberately NOT done (with reasons)
- **Host allow-listing in the id parser** — it is S-G7 (egress scoping, Phase 3→4)
  and would change navigation behaviour, which needs the live gate. Documented as
  an explicit residual (§2.14 audit note) instead of being smuggled in.
- **Raising `min_delay_between_ms` in config** — the pacing floor is applied in
  code; the config value remains the operator's to raise (default 30 ms is kept so
  the diff does not silently change documented policy).
- **`StageRunner` stage names** (caller-supplied, internal only today) and
  **in-process actor trust (TM-03)** remain open by design; they need the
  Phase 3 CLI unification / S-G9 boundary, not a patch here.
