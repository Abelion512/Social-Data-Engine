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

## Pluggable providers — tambah platform tanpa sentuh core

Registry + URL-dispatch sudah jadi kontrak inti (`src/harness/registry.py`):
platform baru = satu file plugin yang meregistrasi adapter + URL regex.

**Cara tercepat (copy-paste-modify):**

```bash
cp -r plugins/example plugins/myplatform
mv plugins/myplatform/example.py plugins/myplatform/myplatform.py
# edit 3 bagian: URL_PATTERN, probe(), collect()
```

Kontrak plugin per modul:

| Bagian | Wajib | Catatan |
|---|---|---|
| `URL_PATTERN` | ✅ | regex routing URL → adapter (first match wins; registrasi terakhir override) |
| adapter | ✅ | subclass `ProviderAdapter` (atau `AgentProvider` untuk browser agent); `collect()` **wajib** mengembalikan `List[Observation]` canonical — satu schema untuk semua platform |
| registrasi | ✅ | `harness.register("myplatform", URL_PATTERN, MyAdapter())` saat import |
| deps | ✅ | import pihak-3 harus ada di `requirements.txt` (gate CI step 8 scan `plugins/` juga) |
| test | ✅ | minimal 1 suite assert di `tests/` (contoh: `tests/test_plugins.py`) |

Verifikasi tanpa browser:

```bash
bash scripts/preflight.sh                          # semua gate
.venv/bin/python -m src.tiktok_linkedin --list-plugins   # introspeksi registry
```

`--list-plugins` menampilkan provider terdaftar + modul plugin + **load errors**;
plugin yang gagal import tidak pernah membungkam run — errornya tampil, author
memperbaiki. Direktori plugin tambahan: env `PLUGIN_PATHS` (dipisah `:`).
Demo end-to-end deterministic: plugin `example` (routing, probe, collect).

## SDE as a plugin — host-agnostic, dua transport

Arah sebaliknya: host mana pun bisa memakai SDE tanpa clone repo. **Repo ini
adalah plugin, bukan integrasi untuk satu host tertentu** — tidak ada kode,
path, maupun tes di sini yang mengasumsikan sebuah agent.

```bash
# A. stdio MCP (host MCP standar: Claude/Cursor/IDE agent/`mcp` CLI)
.venv/bin/python -m src.mcp_server
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python -m src.mcp_server

# B. Streamable HTTP MCP (host yang hanya bisa URL)
.venv/bin/python -m src.mcp_http --port 8765
curl -s localhost:8765/health

# C. Paket plugin generik (manifest + adapter JS + installer)
bash integrations/plugin/install.sh --dir <folder-plugin-host> --dry-run
```

Tools: `sde_list_providers`, `sde_probe`, `sde_collect`, `sde_run_status`
(status/provenance run dari artefak yang sudah ada — read-only). Subset protokol
MCP resmi (`initialize`, `tools/list`, `tools/call`, `ping`); error per-request
fail-closed, server tetap hidup.

Kontrak test: `tests/test_mcp_server.py`, `tests/test_mcp_http_security.py`,
`tests/test_plugin_host.py`, `tests/test_import_layering.py`,
`tests/test_run_status.py`. Panduan host (termasuk batasan jujur + status host
yang **belum terverifikasi**): [`docs/INTEGRATIONS/PLUGIN.md`](INTEGRATIONS/PLUGIN.md).
Jangan duplikasi logika tool di JS — `index.js` hanya adapter yang memanggil
`src/mcp_server.py`.

## Optional agent tooling (assistant-side) — verified 2026-09-21

| Tool | What it gives an agent here | Enable (run once, on your machine) |
|---|---|---|
| [graphify](https://github.com/Graphify-Labs/graphify) | Queryable knowledge graph of this codebase (tree-sitter AST, local, deterministic) — `graphify explain "canonical_runner"` instead of grepping | `uv tool install graphifyy && graphify install --project` → type `/graphify .` in your assistant |
| [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) | Assistant drives the real Chrome over CDP for live tests (click/scroll/inspect) — complements `src/browser_selector.py`, same `--remote-debugging-port` attach model | `npx chrome-devtools-mcp@latest` (MCP server; point your agent client at it) |
| [abelink](https://github.com/Abelion512/abelink) | **UNVERIFIED — jangan klaim kompatibel.** Kontraknya pernah dibaca dari source (`main` @ `6957a79`, 2026-09-21): plugin folder `plugin.json` + `index.js`, MCP client HTTP-only, timeout 20 s. Paket plugin generik di repo ini bisa diadaptasi (contoh 12 baris di dokumen), tapi **belum pernah dijalankan di instance Abelink** dan Abelink belum lolos test-nya sendiri | contoh adapter: [`docs/INTEGRATIONS/PLUGIN.md §4`](INTEGRATIONS/PLUGIN.md) |

These two tools are optional, assistant-side, and never imported at runtime — no `requirements.txt` entry, no runtime import (dependency gate stays green). Do not commit their outputs (`graphify-out/` stays untracked). The `abelink` row is **not** an integration: it is a note, kept UNVERIFIED until someone actually runs it (see the table).

## Where the gates live

| Gate | Script |
|---|---|
| All 8 in one | `scripts/preflight.sh` |
| Ponytail ledger | `scripts/check_ponytail_ledger.py` |
| Dependency gate | `scripts/check_dependencies.py` |
| CI definition | `.github/workflows/ci.yml` (steps 1–8) |
