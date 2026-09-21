#!/usr/bin/env python3
"""
MCP over Streamable HTTP — the SAME tool surface as `src.mcp_server`, HTTP transport.

Why this file exists: some hosts can only reach an MCP server over HTTP (they
POST JSON-RPC to a URL and never spawn a child process), while
`src/mcp_server.py` speaks stdio. This is a transport adapter only: every
request goes through `src.mcp_server.handle_request`, so routing, validation,
bounds and error codes stay in one place.

Protocol behaviour a real client depends on (each covered by
`tests/test_plugin_host.py`):
  - `initialize` / `tools/list` / `tools/call` per POST, response `application/json`
    (plain JSON is a valid Streamable-HTTP reply; SSE is not emitted)
  - `notifications/initialized` (no `id`) → `202 Accepted`, empty body
  - clients may re-`initialize` before every call → this server is stateless
  - JSON-RPC errors answer `200` + an `error` object; non-2xx means transport-level

Security posture (threat model §3.D / TM-29; policies/SECURITY.md):
  - binds loopback by default; a non-loopback host additionally needs
    `--allow-remote` (explicit opt-in)
  - **`Host` must be loopback** → a DNS-rebinding page cannot reach the tools
    through an attacker-controlled hostname
  - **`Content-Type: application/json` is required** → a cross-origin `fetch()`
    with a CORS-safelisted type (`text/plain`) cannot trigger a tool call
  - optional bearer token, compared in constant time; never logged
  - `Content-Length` is required and capped; the socket has a read timeout, so a
    half-open request cannot pin a worker forever
  - the tool surface is read-only (registry / probe / bounded collect / run
    status) — no login, no credential handling, no arbitrary execution

Run:
    .venv/bin/python -m src.mcp_http --port 8765
    curl -s localhost:8765/health
    curl -s -X POST localhost:8765/mcp -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from src.mcp_server import (
    MAX_OUTPUT_ITEMS,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_SCHEMAS,
    handle_request,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PATH = "/mcp"
MAX_BODY_BYTES = 1024 * 1024      # 1 MiB — a tools/call payload is a few hundred bytes
SOCKET_TIMEOUT_S = 30             # read timeout per request (slow-client guard)
MAX_ECHO_CHARS = 200              # never echo a long attacker-supplied path back

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
JSON_CONTENT_TYPE = "application/json"


def _is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def _host_is_loopback(header_value: str) -> bool:
    """True when a `Host: host:port` header names the loopback interface."""
    value = (header_value or "").strip().lower()
    if not value:
        return False
    if value.startswith("["):            # [::1]:8765
        return value[1:value.find("]")] == "::1" if "]" in value else False
    host = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return _is_loopback(host)


def _jsonrpc_error(rid: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _short(value: str) -> str:
    text = str(value or "")
    return text if len(text) <= MAX_ECHO_CHARS else text[:MAX_ECHO_CHARS] + "…"


class _Handler(BaseHTTPRequestHandler):
    """One HTTP request → one JSON-RPC response (transport only)."""

    protocol_version = "HTTP/1.1"     # keep-alive; clients POST per call
    timeout = SOCKET_TIMEOUT_S        # socket read timeout (slowloris guard)
    server_version = f"{SERVER_NAME}-http/{SERVER_VERSION}"
    path_mcp = DEFAULT_PATH
    token: Optional[str] = None
    quiet = False

    # ── plumbing ─────────────────────────────────────────────────────────
    def _send(self, status: int, payload: Optional[Dict[str, Any]],
              extra_headers: Optional[Dict[str, str]] = None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _deny(self, status: int, message: str, extra_headers: Optional[Dict[str, str]] = None) -> None:
        """Refuse a request. Every refusal happens *before* the body is read.

        This connection is HTTP/1.1 (keep-alive), so leaving an unread body on
        the socket would let its bytes be parsed as the next request — a
        request/response desync that can smuggle a call past the `Host`,
        `Content-Type` and size checks on the very connection that just failed
        them. Closing is the fix; draining is not, because an oversized body is
        exactly the resource sink the cap exists to refuse (V8).
        """
        self.close_connection = True
        headers = {"Connection": "close", **(extra_headers or {})}
        self._send(status, _jsonrpc_error(None, -32001 if status == 401 else -32600, message),
                   headers)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 — stdlib hook name
        if not self.quiet:
            sys.stderr.write(f"[sde-mcp-http] {self.address_string()} {fmt % args}\n")

    def _authorized(self) -> bool:
        if not self.token:
            return True
        bearer = self.headers.get("Authorization", "")
        presented = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
        if not presented:
            presented = self.headers.get("X-SDE-Token", "").strip()
        return bool(presented) and hmac.compare_digest(presented, self.token)

    def _content_length(self) -> Tuple[Optional[int], Optional[str]]:
        """(length, error). Chunked bodies are refused: we do not decode them."""
        if "chunked" in (self.headers.get("Transfer-Encoding", "") or "").lower():
            return None, "Transfer-Encoding: chunked tidak didukung — kirim Content-Length"
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0, None
        try:
            length = int(raw)
        except ValueError:
            return None, f"Content-Length bukan angka: {_short(raw)}"
        if length < 0:
            return None, "Content-Length negatif"
        if length > MAX_BODY_BYTES:
            return None, f"body melebihi {MAX_BODY_BYTES} byte"
        return length, None

    # ── routes ───────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 — stdlib hook name
        if self.path.split("?")[0] == "/health":
            self._send(200, {
                "ok": True,
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "transport": "http",
                "auth": "token" if self.token else "none",
                "toolCount": len(TOOL_SCHEMAS),
            })
            return
        self._deny(405, f"GET tidak didukung — POST ke {_short(self.path_mcp)}",
                   {"Allow": "POST"})

    def do_OPTIONS(self) -> None:  # noqa: N802 — stdlib hook name
        self._deny(405, "OPTIONS tidak didukung — server ini tidak mengirim header CORS",
                   {"Allow": "POST"})

    def do_POST(self) -> None:  # noqa: N802 — stdlib hook name
        if self.path.split("?")[0] != self.path_mcp:
            self._deny(404, f"path tidak dikenal: {_short(self.path)}")
            return
        # DNS-rebinding defence: the tools answer loopback names only.
        if not _host_is_loopback(self.headers.get("Host", "")):
            self._deny(400, "Host harus loopback (127.0.0.1 / localhost / ::1)")
            return
        if not self._authorized():
            self._deny(401, "token tidak valid atau belum dikirim")
            return
        content_type = (self.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        if content_type != JSON_CONTENT_TYPE:
            # A CORS-safelisted type (text/plain, form-encoded) can be POSTed
            # cross-origin without a preflight — refuse anything but JSON.
            self._deny(415, f"Content-Type harus {JSON_CONTENT_TYPE} (dapat: {_short(content_type or 'kosong')})")
            return
        length, error = self._content_length()
        if error is not None:
            self._deny(413, error)
            return
        raw = self.rfile.read(length or 0)
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(200, _jsonrpc_error(None, -32700, f"parse error: {exc}"))
            return
        response = handle_request(request)
        if not response:  # notification (no `id`) — 202, no body
            self._send(202, None)
            return
        self._send(200, response)


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 path: str = DEFAULT_PATH, token: Optional[str] = None,
                 quiet: bool = False, allow_remote: bool = False) -> ThreadingHTTPServer:
    """Build (do not start) the HTTP MCP server — also used by the test suite."""
    if not _is_loopback(host) and not allow_remote:
        raise ValueError(
            f"host '{host}' bukan loopback — jalankan dengan --allow-remote bila memang "
            "ingin mengekspos koleksi ke jaringan"
        )
    if not path.startswith("/"):
        raise ValueError(f"path harus dimulai '/': {path!r}")
    handler = type("_BoundHandler", (_Handler,), {
        "path_mcp": path,
        "token": token,
        "quiet": quiet,
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m src.mcp_http",
        description="SDE MCP server over Streamable HTTP (same tools as src.mcp_server).",
    )
    p.add_argument("--host", default=DEFAULT_HOST, help=f"bind host (default {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"POST path (default {DEFAULT_PATH})")
    p.add_argument("--token", default=None,
                   help="wajibkan bearer token (Authorization/X-SDE-Token); "
                        "lebih baik pakai env SDE_HTTP_TOKEN — argv terlihat di `ps`")
    p.add_argument("--quiet", action="store_true", help="matikan log per-request")
    p.add_argument("--allow-remote", action="store_true",
                   help="izinkan bind non-loopback (default tolak)")
    return p.parse_args(argv)


def _resolve_token(args_token: Optional[str],
                   environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Token from the flag, else `SDE_HTTP_TOKEN`; blank means "no token required".

    `--token` is readable by any local process through `ps` /
    `/proc/<pid>/cmdline`, and the bridge deliberately has no other isolation
    (TM-29), so the environment variable is the documented way in. The flag still
    wins when both are set (explicit > ambient).
    """
    env = os.environ if environ is None else environ
    if args_token is not None:
        if not args_token.strip():
            # An empty/blank flag must not silently mean "no auth": fail closed.
            raise ValueError("--token kosong — hapus flag-nya, atau isi token sungguhan")
        return args_token.strip()
    return (env.get("SDE_HTTP_TOKEN") or "").strip() or None


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    try:
        server = build_server(args.host, args.port, args.path, _resolve_token(args.token),
                              args.quiet, args.allow_remote)
    except (ValueError, OSError) as exc:
        print(f"❌ tidak bisa start: {exc}", file=sys.stderr)
        return 2

    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}{args.path}"
    print(f"{SERVER_NAME} (http) ready — {url}", file=sys.stderr)
    print(f"  tools: {', '.join(t['name'] for t in TOOL_SCHEMAS)}"
          f" · max_items ceiling {MAX_OUTPUT_ITEMS}", file=sys.stderr)
    print("  host non-loopback ditolak (DNS-rebinding); content-type wajib application/json",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{SERVER_NAME} (http) stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
