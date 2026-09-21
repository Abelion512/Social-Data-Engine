# Host integration — SDE as a plugin for any MCP-capable host

**Status:** IMPLEMENTED + deterministic-tested (`tests/test_plugin_host.py`,
`tests/test_mcp_http_security.py`, `tests/test_import_layering.py`).
**Scope:** this repo is a *plugin*, not a host. Nothing here depends on, mirrors,
or configures a specific agent; a host consumes the surfaces below through a
small adapter it owns.

Supersedes the 2026-09-21 Abelink-shaped draft (`integrations/abelink/`), which
was restructured into the host-agnostic package described here — see
`docs/PLANNED/sessions/2026-09-21_plugin-hardening-perf.md` for why.

---

## 1. What ships

| Artifact | Role |
|---|---|
| `src/mcp_server.py` | MCP server over **stdio** (JSON-RPC 2.0, one request per line). The tool implementations live here. |
| `src/mcp_http.py` | The same server over **Streamable HTTP** (POST `/mcp`, JSON replies), for hosts that can only reach a URL. Transport only — it calls `mcp_server.handle_request`. |
| `src/run_status.py` | Read-only run/provenance reader behind the `sde_run_status` tool. |
| `integrations/plugin/manifest.json` | Host-agnostic declaration: identity, requirements, transports. Deliberately **does not** list tools (see §3). |
| `integrations/plugin/index.js` | Generic JS adapter (`manifest`, `listTools`, `invoke`, `invokeText`, `handlers`, CLI) — `node:` builtins only, no shell. |
| `integrations/plugin/install.sh` | Copies the plugin into a host's plugin folder and writes `plugin.runtime.json` (absolute repo root + interpreter). |

## 2. Tool surface

| Tool | Args | Returns |
|---|---|---|
| `sde_list_providers` | — | registered providers, loaded plugins, plugin load errors |
| `sde_probe` | `url` | routing provider + cheap metadata, no collection |
| `sde_collect` | `url`, `max_items` (≤1000) | bounded canonical `Observation` records |
| `sde_run_status` | `video_id` (optional), `max_runs` (≤50) | checkpoint status, coverage, termination reason, curated count, improvement iterations, loop outcome — read from artifacts that already exist |

All four are **read-only**: no login, no credential handling, no arbitrary
execution, no writes (`policies/ACCEPTABLE-USE.md`).

## 3. One tool list, two transports

The manifest never duplicates the tool list; a host asks the server:

```bash
.venv/bin/python -m src.mcp_server                       # stdio MCP
python -m src.mcp_server <<< '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
.venv/bin/python -m src.mcp_http --port 8765             # HTTP MCP
curl -s localhost:8765/health
```

`tests/test_plugin_host.py` fails CI if a tool name reappears in `manifest.json`,
so the surface cannot drift from `src/mcp_server.TOOL_SCHEMAS`.

## 4. Consuming it

**A. Host speaks MCP over stdio** — point it at `{python} -m src.mcp_server`
(cwd = repo root, or set `SDE_ROOT`). This is the recommended transport: no
listen socket, no timeout imposed by a bridge layer, works while a collection runs.

**B. Host speaks MCP over HTTP only** — start `python -m src.mcp_http --port 8765`
and register `http://127.0.0.1:8765/mcp`. If the host can send an
`Authorization: Bearer` header, set the token through `SDE_HTTP_TOKEN` rather
than `--token <secret>` — argv is world-readable via `ps` (see §5). Note the
transport-level caveat in §6.

**C. Host loads a plugin folder** — install and let the host's loader read it:

```bash
bash integrations/plugin/install.sh --dir <host plugin folder>   # --dry-run first
node <host plugin folder>/index.js --list-tools                  # sanity check
```

The adapter resolves the engine from `SDE_ROOT`/`SDE_PYTHON`, else
`plugin.runtime.json`, else `<repo>/.venv/bin/python`, else `python3`.

**D. Host just shells out** — `node index.js <tool> '<json args>'` prints the
payload as JSON, e.g. `node index.js sde_probe '{"url":"…"}'`.

### Host-specific adapter (the only host-shaped code, and it lives in the host)

Some hosts require their own manifest format and handler convention. Wrap the
adapter instead of copying its logic:

```js
// <host plugin folder>/index.js — ~12 lines, no SDE logic duplicated
const sde = require('../../path/to/sde/integrations/plugin/index.js')
module.exports = {
  'sde-probe':   async ({ query }) => JSON.stringify(await sde.invoke('sde_probe',   { url: query })),
  'sde-collect': async ({ query }) => JSON.stringify(await sde.invoke('sde_collect',  JSON.parse(query))),
  'sde-status':  async ({ query }) => JSON.stringify(await sde.invoke('sde_run_status', { video_id: query }))
}
```

## 5. Security posture (hardened 2026-09-21)

A local HTTP endpoint is reachable by anything running as this user — including a
web page in a browser — so the bridge refuses rather than trusts:

| Control | Why |
|---|---|
| Loopback bind by default; `--allow-remote` is the only way out | never expose collection on a network by accident |
| `Host` header must be loopback | DNS rebinding: an attacker hostname resolving to 127.0.0.1 is rejected `400` |
| `Content-Type: application/json` required | a cross-origin `fetch()` with a CORS-safelisted type (`text/plain`) must not be able to trigger a tool call (`415`) |
| `Content-Length` required and capped (1 MiB); chunked bodies refused; 30 s socket timeout | resource exhaustion / half-open clients |
| Optional bearer token, constant-time compare, never logged; `SDE_HTTP_TOKEN` preferred over `--token` (argv is visible in `ps`), and a blank token fails closed instead of silently disabling auth | shared-machine isolation |
| `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` | no cached/sniffed tool output |
| Errors echo a truncated path only; `url` rejects control characters | log/response injection (TM-30) |
| JS adapter: `node:` builtins only, `execFile` argv, no `shell: true` | an agent-supplied URL is never interpolated into a shell |
| `install.sh` writes its config with the interpreter's `json.dump`, and refuses paths a JSON file cannot represent | a quote/backslash in a path cannot corrupt the config or inject extra keys |

Threat-model rows: TM-29 (local HTTP bridge), TM-30 (identifier/log injection
through tool args) in `docs/SECURITY-THREAT-MODEL.md`.

## 6. Honest limits

1. **A real collection through a host is not proven here.** The deterministic
   layer is tested (manifest, adapter, transports, hardening, run status,
   JS→Python boundary); browser collection still needs the desktop
   human-in-the-loop gate in `agents.md`.
2. **Some hosts cap an HTTP tool call (e.g. 20 s)**, while a browser collection
   takes minutes. Use the stdio transport for collection; the HTTP bridge is for
   listing/probing/status. A `collect_start` + `run_status` job pair would remove
   the caveat — deliberately deferred, because spawning a background process is a
   `process.execute`-class capability that `policies/SECURITY.md` and
   `policies/SAFETY.md` say must not be a default grant.
3. **Guest sessions plateau at 22–36 % coverage** — unchanged by any of this.
4. **URLs are routed by provider regex with no host allow-list** (S-G7 egress
   scoping, TM-17). Deliberate: tightening it changes navigation and therefore
   requires the live gate.
5. **Plugins are not sandboxed.** Installing this folder means a host runs that
   JS with user privileges; `policies/SECURITY.md` lists plugin isolation as out
   of scope until designed.
6. **No published package.** A host must clone this repo (or copy the folder).
   `pyproject.toml`/packaging is Phase 6.

## 7. Known hosts

| Host | Status | Note |
|---|---|---|
| Any MCP client (Claude Desktop, Cursor, IDE agents, `mcp` CLI) | supported by construction | stdio MCP is the standard flow; no host-specific code needed |
| Shell scripts, cron, notebooks | supported | `python -m src.mcp_server` (line JSON) or `node index.js <tool> '{…}'` |
| [Abelink](https://github.com/Abelion512/abelink) (Tauri v2 + Bun desktop agent) | **UNVERIFIED — do not claim compatibility** | Its loader/MCP-client contract was read at `main` @ tree `6957a79` on 2026-09-21 (folder plugins `plugin.json` + `index.js`, handler map called as `{query}`, MCP client is Streamable HTTP only, 20 s call timeout). The generic package can be adapted to it with the §4 snippet, but it has **never been executed inside a running Abelink instance** and Abelink has not passed its own test suite. Re-read their source before relying on any of this. |

## 8. Verification

```bash
bash scripts/preflight.sh .venv/bin/python          # all 8 gates, 25 suites
.venv/bin/python tests/test_plugin_host.py          # plugin package contract (17)
.venv/bin/python tests/test_mcp_http_security.py    # HTTP transport + hardening (21)
.venv/bin/python tests/test_import_layering.py      # NFR-007 + host-path budget (9)
.venv/bin/python tests/test_run_status.py           # provenance reader + manifest stamp (12)
```

Deterministic and offline: the HTTP suites use loopback sockets only, the
node checks self-skip when node is absent, and no suite needs a browser, a
credential or the network.
