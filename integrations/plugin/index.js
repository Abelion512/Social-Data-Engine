#!/usr/bin/env node
/**
 * Social Data Engine (SDE) — generic host plugin adapter.
 *
 * Host-agnostic on purpose: this file knows NOTHING about any particular
 * desktop agent. It exposes three plain entry points and a CLI, and every host
 * consumes whichever one it supports:
 *
 *   const sde = require('./index.js')
 *   sde.manifest            // parsed manifest.json (identity + transports)
 *   await sde.listTools()   // [{name, description, inputSchema, …}] from the server
 *   await sde.invoke(name, args)   // tools/call → structured payload (JS object)
 *   sde.handlers            // { toolName: (args) => Promise<object> } convenience map
 *
 * CLI (usable from a shell, a script, or a host that shells out):
 *   node index.js --list-tools
 *   node index.js sde_probe '{"url":"https://example.social/post/123"}'
 *
 * It holds no collection logic: each call runs one JSON-RPC request through the
 * repo's MCP server (`src/mcp_server.py`), so routing/validation/bounds stay in
 * one implementation for every host.
 *
 * Failure is never disguised as an empty success (policies/TRANSPARENCY.md): a
 * missing interpreter, a bad URL or a server-side error throws with the reason.
 */
'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { execFile } = require('node:child_process')

const RPC_TIMEOUT_MS = 120000
const MAX_TEXT = 20000 // tool output is text; keep one call inside a sane budget
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024
const MAX_CONFIG_BYTES = 64 * 1024

/** Parsed manifest.json next to this file (empty object when unreadable). */
function readManifest() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8')) || {}
  } catch (e) {
    throw new Error(`manifest.json tidak terbaca: ${e.message}`)
  }
}

const manifest = readManifest()

/**
 * Where the repo + interpreter are. Precedence:
 *   1. env `SDE_ROOT` / `SDE_PYTHON` (explicit override)
 *   2. `plugin.runtime.json` next to this file — written by install.sh
 *   3. `<repo>/.venv/bin/python`, else `python3` on PATH
 */
function resolveConfig() {
  let fromFile = {}
  const runtimeFile = path.join(__dirname, 'plugin.runtime.json')
  try {
    if (fs.statSync(runtimeFile).size <= MAX_CONFIG_BYTES) {
      fromFile = JSON.parse(fs.readFileSync(runtimeFile, 'utf8')) || {}
    }
  } catch {
    fromFile = {} // absent or unreadable: fall through to env/PATH
  }
  const repoRoot = String(process.env.SDE_ROOT || fromFile.repo_root || '').trim()
  let python = String(process.env.SDE_PYTHON || fromFile.python || '').trim()
  if (python && /[\u0000\n\r]/.test(python)) {
    throw new Error('SDE_PYTHON/plugin.runtime.json berisi karakter kontrol yang tidak sah')
  }
  if (!python) {
    const venvPython = repoRoot ? path.join(repoRoot, '.venv', 'bin', 'python') : ''
    python = venvPython && fs.existsSync(venvPython) ? venvPython : 'python3'
  }
  if (!repoRoot || !fs.existsSync(path.join(repoRoot, 'src', 'mcp_server.py'))) {
    throw new Error(
      'repo Social Data Engine tidak ditemukan. Set env SDE_ROOT ke folder repo, atau '
        + 'jalankan `bash integrations/plugin/install.sh --dir <folder-plugin-host>` '
        + 'ulang agar plugin.runtime.json ditulis.'
    )
  }
  return { repoRoot, python }
}

/** Run exactly one JSON-RPC call through `python -m src.mcp_server` (stdio). */
function rpc(method, params) {
  const { repoRoot, python } = resolveConfig()
  const request = JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: params || {} })
  return new Promise((resolve, reject) => {
    const child = execFile(
      python,
      ['-m', 'src.mcp_server'],
      {
        cwd: repoRoot,
        timeout: RPC_TIMEOUT_MS,
        maxBuffer: MAX_OUTPUT_BYTES,
        env: { ...process.env, PYTHONPATH: repoRoot }
      },
      (err, stdout, stderr) => {
        if (err) {
          const detail = String(stderr || err.message || '').trim().slice(0, 500)
          reject(new Error(`SDE (${python} -m src.mcp_server) gagal: ${detail || err.code}`))
          return
        }
        const lines = String(stdout).split('\n').filter((line) => line.trim())
        if (lines.length === 0) {
          reject(new Error('SDE MCP server tidak mengembalikan respons apa pun.'))
          return
        }
        let response
        try {
          response = JSON.parse(lines[lines.length - 1])
        } catch {
          reject(new Error(`Respons SDE bukan JSON: ${lines[lines.length - 1].slice(0, 200)}`))
          return
        }
        if (response.error) {
          reject(new Error(`SDE ${method}: ${response.error.message || 'error tak dikenal'}`))
          return
        }
        resolve(response.result || {})
      }
    )
    child.stdin.end(request + '\n') // EOF ends the server loop deterministically
  })
}

/** Tool catalog from the server (never a copy) — `[{name, description, inputSchema}]`. */
async function listTools() {
  const result = await rpc('tools/list', {})
  return Array.isArray(result.tools) ? result.tools : []
}

/** Invoke one tool. Returns the raw structured payload (throws on tool error). */
async function invoke(toolName, args) {
  const name = String(toolName || '').trim()
  if (!name) throw new Error('nama tool kosong')
  const result = await rpc('tools/call', { name, arguments: (args && typeof args === 'object') ? args : {} })
  if (result.isError) {
    throw new Error(`SDE ${name}: tool melaporkan error`)
  }
  return result.structured ?? result
}

/** Same payload as text (truncated), for hosts whose tool channel is a string. */
async function invokeText(toolName, args) {
  const payload = await invoke(toolName, args)
  const text = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2)
  return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}\n…[dipotong pada ${MAX_TEXT} karakter]` : text
}

/** Convenience map for hosts that want `name -> handler` (generic, no host naming). */
const handlers = Object.create(null)
for (const tool of ['sde_list_providers', 'sde_probe', 'sde_collect', 'sde_run_status']) {
  handlers[tool] = (args) => invoke(tool, args)
}

/** CLI: `node index.js --list-tools` | `node index.js <tool> '<json args>'`. */
async function main(argv) {
  const [first, second] = argv
  if (!first || first === '--help' || first === '-h') {
    process.stdout.write(
      'usage: node index.js --list-tools\n'
      + "       node index.js <tool> '<json args>'\n"
    )
    return 0
  }
  if (first === '--list-tools' || first === '--tools') {
    process.stdout.write(JSON.stringify(await listTools(), null, 2) + '\n')
    return 0
  }
  let args = {}
  if (second) {
    try {
      args = JSON.parse(second)
    } catch (e) {
      process.stderr.write(`args bukan JSON: ${e.message}\n`)
      return 2
    }
  }
  process.stdout.write(JSON.stringify(await invoke(first, args), null, 2) + '\n')
  return 0
}

module.exports = { manifest, listTools, invoke, invokeText, handlers, main, resolveConfig }

if (require.main === module) {
  main(process.argv.slice(2))
    .then((code) => process.exit(code))
    .catch((err) => {
      process.stderr.write(String(err && err.message ? err.message : err) + '\n')
      process.exit(1)
    })
}
