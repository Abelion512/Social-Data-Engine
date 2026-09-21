#!/usr/bin/env python3
"""
SDE MCP server — Social Data Engine as a PLUGIN for any MCP-capable host.

Bukti konsep "SDE cocok jadi plugin": server ini expose fungsionalitas
`src/harness.registry.harness` lewat protokol MCP (subset resmi: JSON-RPC 2.0,
newline-delimited JSON di stdio — satu request JSON per baris, satu respons
JSON per baris).

Methods:
  initialize      → server capabilities (MCP handshake)
  tools/list      → katalog tools
  tools/call      → jalankan tool
  ping            → liveness

Tools (semua deterministic-testable tanpa browser):
  sde_list_providers   → {providers, plugins, load_errors} — registry snapshot
  sde_probe            → {provider, accessible, metadata} untuk satu URL
  sde_collect          → canonical Observations dari URL (demo provider =
                         deterministic; provider browser butuh session live)
  sde_run_status       → status/provenance satu run dari artefak yang SUDAH ada
                         (checkpoint, coverage, termination reason, curated count,
                         improve iterations) — read-only, tanpa browser

Dukungan host nyata: MCP asli (Claude/Cursor) pakai ContentBlock — `tools/call`
di sini mengembalikan {content:[{type:"text", text:<json string>}], isError}
plus `structured` (data mentah) untuk konsumen programatik/non-MCP.

Konvensi repo yang diikuti:
  - TRANSPARENCY.md: error TIDAK dibungkam — isError=true + pesan eksplisit.
  - ACCEPTABLE-USE.md: server hanya expose pembacaan (probe/collect);
    tidak ada endpoint login/interaksi.
  - Stdlib only (gate dependency).

Run:
    .venv/bin/python -m src.mcp_server            # serve di stdio
    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python -m src.mcp_server
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict

from src.harness.registry import harness
from src.plugins import list_plugins as _plugin_introspection

SERVER_NAME = "sde-mcp"
SERVER_VERSION = "0.2.0"        # 0.2.0: additive `sde_run_status` tool
PROTOCOL_VERSION = "2024-11-05"  # MCP protocol version we implement

MAX_URL_LEN = 2048          # reject absurd inputs before they touch anything
MAX_OUTPUT_ITEMS = 1000     # hard cap on records returned in one call


# ── Tool implementations (thin wrappers over the shared Harness) ────────────
def _tool_list_providers(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Registry snapshot: builtin providers + loaded plugins + load errors."""
    info = _plugin_introspection()
    return {
        "providers": info["registered"],
        "plugins": info["loaded_plugin_modules"],
        "plugin_dirs": info["dirs"],
        "load_errors": info["load_errors"],
    }


def _validate_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url wajib string non-kosong")
    if len(url) > MAX_URL_LEN:
        raise ValueError(f"url terlalu panjang (>{MAX_URL_LEN})")
    cleaned = url.strip()
    # Control characters would land in logs/error messages and let a caller forge
    # extra lines in any line-oriented consumer (log injection): refuse outright.
    if any(ch in cleaned for ch in "\r\n\x00"):
        raise ValueError("url mengandung karakter kontrol (\\r \\n \\x00)")
    return cleaned


def _tool_probe(args: Dict[str, Any]) -> Dict[str, Any]:
    url = _validate_url(args.get("url"))
    result = harness.probe(url)
    result["provider_resolved"] = harness.resolve(url)
    return result


def _tool_collect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Async-dispatch collect → canonical Observation list (bounded output)."""
    import asyncio

    url = _validate_url(args.get("url"))
    max_items = int(args.get("max_items", 10) or 10)
    if max_items < 1:
        raise ValueError("max_items harus >= 1")
    max_items = min(max_items, MAX_OUTPUT_ITEMS)

    obs = asyncio.run(harness.collect(url, max_comments=max_items))
    items = [o.to_dict() for o in obs[:max_items]]
    return {
        "provider": harness.resolve(url),
        "url": url,
        "count": len(items),
        "truncated": len(obs) > len(items),
        "observations": items,
    }


def _tool_run_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only run/provenance status from artifacts already on disk.

    `src.run_status` is imported here (not at module scope) so a host that only
    lists tools never pays for the runtime/policy imports.
    """
    from src.run_status import MAX_RUNS, collect_run_status

    video_id = args.get("video_id")
    if video_id is not None and not isinstance(video_id, str):
        raise ValueError("video_id harus string")
    raw_max = args.get("max_runs")
    max_runs = MAX_RUNS if raw_max is None else int(raw_max)
    if max_runs < 1:
        raise ValueError("max_runs harus >= 1")
    return collect_run_status(video_id=video_id, max_runs=max_runs)


TOOLS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "sde_list_providers": _tool_list_providers,
    "sde_probe": _tool_probe,
    "sde_collect": _tool_collect,
    "sde_run_status": _tool_run_status,
}
# tool name → module-level function name; dispatch via globals() lookup at call
# time so tests can patch the implementation and the mapping stays a flat table.
TOOL_FNS = {
    "sde_list_providers": "_tool_list_providers",
    "sde_probe": "_tool_probe",
    "sde_collect": "_tool_collect",
    "sde_run_status": "_tool_run_status",
}


def _error_response(rid: Any, code: int, message: str,
                    data: Any = None) -> Dict[str, Any]:
    e: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}

TOOL_SCHEMAS = [
    {
        "name": "sde_list_providers",
        "description": "List registered providers, loaded plugins and load errors.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sde_probe",
        "description": "Cheap availability/metadata check for one URL (no collection).",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "sde_collect",
        "description": "Collect canonical Observations for a URL via the routed "
                       "provider (demo/example provider is deterministic; browser "
                       "providers need a live session).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_OUTPUT_ITEMS},
            },
            "required": ["url"],
        },
    },
    {
        "name": "sde_run_status",
        "description": "Read-only run/provenance status for one video_id (checkpoint "
                       "status, coverage, termination reason, curated record count, "
                       "improvement iterations, loop outcome) or a bounded list of "
                       "recorded runs (max_runs, ceiling 50) when video_id is omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "slug id; omit to list runs"},
                "max_runs": {"type": "integer", "minimum": 1},
            },
        },
    },
]


# ── JSON-RPC 2.0 over stdio (newline-delimited) ─────────────────────────────
def _result_content(payload: Dict[str, Any]) -> list:
    """MCP tools/call result: text ContentBlock carrying the JSON payload."""
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]


def handle_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """Satu JSON-RPC request → satu response object (tanpa I/O)."""
    if not isinstance(req, dict):
        return _error_response(None, -32600, "Invalid Request")

    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    # JSON-RPC 2.0: notification (tanpa id) TIDAK dijawab, metode apa pun —
    # cek ini HARUS sebelum dispatch, bukan hanya untuk metode tak dikenal.
    if "id" not in req:
        return {}

    def _ok(result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _err(code: int, message: str, data: Any = None) -> Dict[str, Any]:
        return _error_response(rid, code, message, data)

    try:
        if method == "initialize":
            return _ok({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "ping":
            return _ok({})
        if method == "tools/list":
            return _ok({"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            fn = globals().get(TOOL_FNS.get(name, ""))
            if fn is None:
                return _err(-32602, f"unknown tool: {name!r}",
                            data={"available": sorted(TOOL_FNS)})
            payload = fn(args)
            return _ok({"content": _result_content(payload),
                        "structured": payload, "isError": False})
        # unknown id'd methods are a protocol error.
        return _err(-32601, f"method not found: {method!r}")
    except Exception as exc:  # noqa: BLE001 — one request never kills the server
        return _err(-32603, f"internal error: {type(exc).__name__}: {exc}")


def serve() -> int:
    """Main loop: read line-JSON from stdin, write line-JSON to stdout.

    Satu request gagal TIDAK menghentikan server (fail-closed per request,
    server tetap hidup) — exit 0 hanya di EOF.
    """
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as e:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": f"parse error: {e}"}}
        else:
            resp = handle_request(req)
        if resp:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


# ── Boot: registry must be complete BEFORE the first request ────────────────
# Load external plugins once at import (same idempotent call in every entry
# point); otherwise a fresh process can route only the builtins.
from src.plugins import load_plugins as _boot_load_plugins  # noqa: E402

_boot_load_plugins()


if __name__ == "__main__":
    sys.exit(serve())
