# Session Log — 2026-09-21 · host plugin (generic) + perf + hardening

## Goal (user request, verbatim)

> 1. Write unit tests · 2. Optimize performance · 3. Update dokumentasi ·
> 4. Find vulnerabilities · 5. fix your suggest and all above ·
> 6. Abelink belum lolos test, jadi jangan memaksa repo ini jadi abelink cukup
> jadi plugin biasa saja.

Point 6 is the scope decision that drives the rest: **this repo is a plugin, not
a host integration for one specific agent.** The previous turn's
`integrations/abelink/` package (a native Abelink plugin, its installer and its
connector payload) was therefore restructured into a host-agnostic
`integrations/plugin/`, and every Abelink-specific claim was demoted to an
explicitly **UNVERIFIED** row in `docs/INTEGRATIONS/PLUGIN.md §7`.

## Anti-Duplication Gate (pre-work)

- Baseline: `bash scripts/preflight.sh .venv/bin/python` → all 8 gates green,
  **21 suites** with the two files a previous session left uncommitted
  (`src/mcp_server.py`, `tests/test_mcp_server.py`) plus `docs/AGENT-GUIDE.md`
  modified. All three were preserved (the MCP server was extended, not rewritten).
- The Abelink contract researched last turn (loader + MCP-client behaviour) is
  kept as a *note*, not as a structural dependency: no file, folder, comment or
  test in this repo now assumes a particular host.

## 1. Unit tests (+4 suites, 1 rewritten)

| Suite | Checks | What it pins |
|---|---|---|
| `tests/test_plugin_host.py` | 17 | manifest identity/schema stamp, "manifest must not duplicate the tool list", transports point at real modules, JS uses `node:` builtins only and never a shell, handler map == real tool names, installer requires `--dir`, `--dry-run` writes nothing, config survives a hostile path, unknown flag rejected, node end-to-end (`--list-tools`, `sde_probe`, error propagation) |
| `tests/test_mcp_http_security.py` | 21 | the whole HTTP surface + every hardening refusal (see §4) |
| `tests/test_import_layering.py` | 9 | NFR-007 provider-blind runtime (static AST + runtime check), host path loads no browser stack, tools/list works without `playwright`/`camoufox`, lazy re-exports still resolve, unknown attribute still raises |
| `tests/test_run_status.py` | 12 | provenance reader on a synthetic workspace + `manifest.v1` stamping |
| `tests/test_mcp_server.py` (extended) | 14 (was 12) | tool list now includes `sde_run_status`; hostile id and `max_runs: 0` fail closed; control characters in `url` rejected |

Deleted: `tests/test_abelink_integration.py` (host-specific) — its generic parts
live in the four suites above.

## 2. Performance — measured, not asserted

Found with `python -X importtime`: importing `src.mcp_server` dragged the whole
browser stack (`src.collector` → `src.browser_selector` → `urllib.request` +
`asyncio` + `playwright` + `camoufox`) even though the host path only routes and
probes. Root causes, each fixed at the source rather than at the call sites:

| Fix | Before → after |
|---|---|
| `src/harness/tools.py`: collector JS snippets were imported at module scope → now `_collector_symbol()` on first use + PEP 562 `__getattr__` for the legacy names | removes `collector` from the path |
| `src/harness/__init__.py`: imported `agent` (→ `human`, `tools`, `collector`) eagerly → now lazy attrs/submodules via `__getattr__`; the registry itself stays eager | removes `agent`/`human` |
| `src/providers/base.py`: `from src.harness.tools import AgentTool` at module scope → lazy `__getattr__` | `import src.providers.base` 84 ms → **27 ms** |
| `src/providers/tiktok.py`: two module-level `try: import` re-exports (`AsyncCamoufox`, `BrowserSession`) that **nothing in the repo imported** → deleted (`delete:` in the Ponytail sense: dead code that cost ~28 ms) | removes `browser_selector` (urllib + asyncio) |
| `src/harness/registry.py`: `import asyncio` at module scope, only used by `collect_url()` → local import | removes `asyncio` (~20 ms) |

Measurements (Python 3.10, same sandbox, 3 runs each):

| Metric | Before | After |
|---|---|---|
| `import src.mcp_server` | 80 ms | **51 ms** |
| one-shot `tools/list` (process start → response) | 85 ms | **51 ms** (1.7×) |
| one-shot `sde_probe` | 85 ms | **50 ms** |
| `import src.providers.base` | 84 ms | **27 ms** (3.1×) |
| browser stack on the host path (`collector`, `browser_selector`, `playwright`, `camoufox`, `asyncio`, `urllib.request`) | all loaded | **none** |

Why this matters beyond speed: a plugin host spawns this process per tool call,
and in an environment without the optional browser packages (CI) listing tools
and probing must still work — `tests/test_import_layering.py` now enforces both.

Behaviour is preserved by construction and checked: legacy attribute paths
(`from src.harness import BrowserAgent`, `from src.providers.base import AgentTool`,
`src.harness.tools.DOM_SCRAPE_JS`) still resolve, and all 25 suites are green.

## 3. Tool surface — provenance made inspectable (my earlier suggestions)

- **`sde_run_status` (new, `src/run_status.py`)** — reads `state/jobs/<id>.json`,
  `state/loops/<id>.json`, `data/manifests/<id>.json`,
  `data/manifests/<id>.improve.jsonl` and the curated tree. No new storage. A
  missing run is `no_run_found`; an existing-but-unreadable artifact is its own
  `unreadable` status with the parse error attached (never collapsed into "no
  data"); ids are validated and paths rooted (S-G2/TM-13); every cap
  (runs/lines/bytes) reports itself.
- **`manifest.v1` stamp (`src/export/manifest.py`)** — `build_manifest` now
  returns `schema_version`, and `write_manifest` stamps per-video files without
  overwriting a caller-provided value (FR-PROV-003/004 step).
- **NFR-007 import audit landed** as a real test (suggestion 5, partially): the
  runtime is now *provably* provider-blind rather than documented as such.
- **`collect_start` still deferred, now with the reason on record**: spawning a
  background collection is a `process.execute`-class capability, and
  `policies/SECURITY.md` + `policies/SAFETY.md` require it to be an approvable
  capability rather than a default grant. `sde_run_status` gives hosts the
  consumer half today.

## 4. Vulnerabilities found and fixed

| # | Finding | Impact | Fix + guard |
|---|---|---|---|
| V1 | **Drive-by tool invocation over HTTP** — the bridge executed tools for any client that could reach the port; a page could DNS-rebind a hostname to 127.0.0.1 and POST JSON with a **CORS-safelisted** `Content-Type: text/plain` (no preflight, and the content type was never checked). | A malicious web page could trigger local tool calls (read-only surface, response unreadable — still an unwanted capability). | `Host` must be loopback (`400`), `Content-Type` must be `application/json` (`415`), plus `nosniff`/`no-store`; `test_non_loopback_host_header_is_refused`, `test_content_type_must_be_json` |
| V2 | **Resource exhaustion / slow clients** — no socket timeout, `Transfer-Encoding: chunked` silently yielded an empty parse, oversize bodies only partially guarded. | A half-open request could pin a worker; chunked requests were misinterpreted rather than refused. | 30 s socket timeout, chunked refused (`413`), `Content-Length` required and capped at 1 MiB, `test_chunked_body_is_refused` / `test_oversized_body_is_refused` / `test_socket_timeout_is_set` |
| V3 | **Log/response injection through tool args** — `url` was only length-checked, so `\r\n`/`NUL` reached error messages and any line-oriented consumer; the HTTP layer echoed an unbounded request path. | Forged log lines; oversized reflection. | `_validate_url` rejects control characters; reflected paths truncated to 200 chars (`test_url_with_control_characters_is_rejected`, `test_reflected_path_is_truncated`) |
| V4 | **Config injection in the installer** — `sde.json`/`plugin.runtime.json` was written by shell interpolation of two filesystem paths, so a `"` or `\` in a path produced invalid JSON (and could inject extra keys, e.g. a different interpreter). | A crafted path could corrupt the plugin's runtime config. | The config is written by the interpreter we already validated via `json.dump`; newline paths are refused (`test_install_and_config_survive_a_hostile_path`, `test_refuses_a_path_a_json_file_cannot_represent`) |
| V5 | **Silent-empty bug in `build_manifest`** (correctness, found by writing the tests) — it globbed `data/curated/*.jsonl` (flat) while the pipeline writes `data/curated/<YYYY-MM-DD>/<id>.jsonl`, so a real corpus reported `total_videos: 0` **with no error** — exactly the "empty success hides a failure" class `policies/TRANSPARENCY.md` forbids. | Any consumer of the manifest silently lost the whole dataset. | Both layouts are read (newest date first, first hit per video id wins, flat last), ids still validated (`test_build_manifest_stamps_the_schema`) |
| V6 | **Traversal through the new status tool** (prevented by design) | — | `require_slug_identifier` + `rooted_file` before any path, hostile filenames skipped when scanning (`test_hostile_video_id_is_refused_before_any_path_is_built`, `test_listing_skips_hostile_filenames`) |
| V7 | **Auth token exposed through `argv`** — the only way to enable the bridge token was `--token <secret>`, readable by every local process via `ps` / `/proc/<pid>/cmdline`, in a same-machine-isolation design where the token *is* the control | A local unprivileged process could read the secret and then call the read-only-but-powerful tool surface | `SDE_HTTP_TOKEN` is the documented path and is used when the flag is absent (flag still wins); a blank flag now fails closed instead of silently meaning "no auth" (`test_token_comes_from_env_and_the_flag_wins`) |
| V8 | **Request/response desync on every refused request** (found by CI, after the workflow repair made the job run for the first time) — every refusal returned *before* the body was read, but the transport is HTTP/1.1 keep-alive, so the unread body stayed on the socket: a caller could fail `Host` / `Content-Type` / size, keep the connection, and have the smuggled bytes parsed as the **next** request — bypassing the perimeter V1 just established. The client also *raced* the close and saw `EPIPE` instead of the refusal, which is exactly why the check passed locally and failed on CI. | A refusal could smuggle a tool call past the checks it had just failed. | `_deny()` ends the connection (`Connection: close` + `close_connection = True`) instead of draining — draining an oversized body is the resource sink the cap exists to refuse. `test_refusals_close_the_connection` (raw socket, 415 + 400 paths) and `test_oversized_body_is_refused` rewritten to announce the length and send no body; both verified to **fail** against the pre-fix handler |

Unchanged known debt (not silently "fixed"): URL host allow-list / egress scoping
(S-G7, TM-17) — it changes navigation, so it belongs to the live gate.

Threat-model rows added: **TM-29** (local HTTP bridge), **TM-30** (identifier /
log injection through tool arguments) and **TM-31** (undrained body on a keep-alive
refusal → request desync).

## 5. Structural change (point 6)

```
integrations/abelink/{plugin.json,index.js,mcp-connector.json,install.sh}   →  deleted
integrations/plugin/{manifest.json,index.js,install.sh}                     →  generic
docs/INTEGRATIONS/ABELINK.md                                                →  docs/INTEGRATIONS/PLUGIN.md
```

The generic package knows no host: `--dir <host plugin folder>` instead of a
hardcoded Abelink path, no `{query}` handler convention in the shipped code (the
12-line host adapter lives in the docs, for the host to own), no connector
payload for a specific agent's channel. Abelink appears once, in the "Known
hosts" table, as **UNVERIFIED — do not claim compatibility**.

## 6. Verification performed

| Gate | Result |
|---|---|
| `bash scripts/preflight.sh .venv/bin/python` | ✅ all 8 gates green, **25 suites** (baseline 21) |
| New suites | plugin_host 17 · mcp_http_security 22 · import_layering 9 · run_status 12 |
| `bash -n integrations/plugin/install.sh` | ✅ |
| Installer dry-run + real install (temp target, quote/backslash path) | ✅ valid config, exact path round-trip |
| node adapter (`--list-tools`, `sde_probe`, error path) | ✅ 3 checks in-suite + 90 ms round-trip |
| Perf measurement | ✅ numbers in §2, enforced by `tests/test_import_layering.py` |
| Dependency gate | ✅ unchanged — new Python is stdlib-only, JS uses `node:` builtins |

**Live-test gate: NOT run** — headless sandbox, and no acquisition code changed
(`src/collector.py`, `src/pipeline/**`, `src/providers/**` untouched apart from
deleting two dead re-exports in `providers/tiktok.py`). The gate stays mandatory
for the first real collect driven from a host, and for any change to routing.

## 7. What I deliberately did not do

- **Not** claim any host works: no host has been executed against this package
  beyond shell/node invocations, so every compatibility statement is labelled
  unverified.
- **Not** build `collect_start` (see §3 rationale).
- **Not** touch the two-pipeline/CLI-unification debt (`CURRENT-STATE` §6.3/§6.8):
  it changes `curated/` output and therefore needs the live gate.
- **Not** vendor or mirror any host's source, and no host-specific fixtures.

## 8. Follow-up — CI repair + V8 (same day)

The user's next request was *"fix ci, and push"*. Two separate problems, only one
of them code:

1. **`.github/workflows/ci.yml` was unparseable.** The step name
   `Ponytail ledger gate — every ponytail: marker has a debt entry` contains an
   unquoted `: ` inside a plain scalar, which YAML reads as a mapping — so the file
   was invalid and **GitHub never started a job**. Four consecutive runs (since
   `eaf2a97`) reported *"This run likely failed because of a workflow file issue"*
   with no log and no annotation, which is indistinguishable from an infra blip.
   Fixed by quoting the scalar (plus a `NOTE`). Verified with `yaml.safe_load` on
   both workflow files, then by reproducing all 8 CI steps locally the strict way
   (the compile step has no `2>/dev/null` fallback in CI, unlike `preflight.sh`).
2. **A billing block, which is not a repo defect.** Earlier runs carried
   *"The job was not started because recent account payments have failed or your
   spending limit needs to be increased."* No workflow edit can override that;
   it needs owner action in GitHub → Settings → Billing & plans. Recorded rather
   than papered over.

Once the workflow became parseable, run `35556655929` started for the first time
since 2026-09-19 and immediately **failed at step 4** — exposing V8, which no local
run could have caught reproducibly because the old check raced the server's close
against a 1 MiB write and won on this machine. That is the argument for keeping CI
honest rather than green-by-absence: the bug was a *live bypass of the guard added
earlier the same day*, and it only surfaced when the gate actually ran.

Re-verified after the fix: 25 suites / 283 assertions, all 8 gates green, the two
new checks confirmed to fail against the pre-fix handler, and
`test_mcp_http_security.py` run 3× plus the thread/socket suites repeated to rule
out further races.
