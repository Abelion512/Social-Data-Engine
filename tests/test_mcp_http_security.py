#!/usr/bin/env python3
"""
HTTP MCP transport + hardening — the executable spec for `src/mcp_http.py`.

The bridge is a local HTTP service that can be reached by anything running as
this user, including a web page in a browser. These checks pin both the protocol
surface a real client needs and the refusals that keep a hostile page out
(threat model TM-29):

  protocol   initialize / tools/list / tools/call, `202` for a notification,
             JSON-RPC errors on `200`, parse errors as `-32700`
  hardening  loopback `Host` required (DNS rebinding), `Content-Type:
             application/json` required (a CORS-safelisted `text/plain` POST must
             not reach a tool), `Content-Length` required and capped, chunked
             bodies refused, `GET /mcp`/`OPTIONS` 405, unknown path 404,
             optional bearer token compared in constant time, `no-store` +
             `nosniff` on every reply, non-loopback bind needs an explicit flag

Stdlib only, deterministic, loopback sockets only (no external network).
"""
import json
import socket
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import mcp_http, mcp_server  # noqa: E402

EXAMPLE_URL = "https://example.social/post/123"


def _rpc(method, params=None, rid=1):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}}).encode()


def _raw_exchange(port, request_head, body=b""):
    """Send a hand-built request and read the reply until the server closes.

    Used only where the *sending* itself is the thing under test: a client that
    pushes a body the server is about to refuse can lose a race and see EPIPE
    instead of the reply (observed on CI), so those checks drive the socket
    directly and never write more than the guard is supposed to read.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(request_head)
        if body:
            sock.sendall(body)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except (ConnectionResetError, socket.timeout):
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", "replace")


def _status_of(raw_response):
    line = raw_response.split("\r\n", 1)[0]
    parts = line.split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


class _ServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = mcp_http.build_server(port=0, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.token_server = mcp_http.build_server(port=0, token="s3cret", quiet=True)
        cls.token_port = cls.token_server.server_address[1]
        cls.token_thread = threading.Thread(target=cls.token_server.serve_forever, daemon=True)
        cls.token_thread.start()

    @classmethod
    def tearDownClass(cls):
        for server, thread in ((cls.server, cls.thread), (cls.token_server, cls.token_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def request(self, method, path="/mcp", body=None, port=None, headers=None):
        url = f"http://127.0.0.1:{port or self.port}{path}"
        req = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), dict(exc.headers)

    def post(self, body, **kwargs):
        headers = {"Content-Type": "application/json", **(kwargs.pop("headers", None) or {})}
        return self.request("POST", body=body, headers=headers, **kwargs)


class TestProtocolSurface(_ServerMixin, unittest.TestCase):
    def test_transport_reuses_the_stdio_core(self):
        """One tool implementation: HTTP must not fork handle_request."""
        self.assertIs(mcp_http.handle_request, mcp_server.handle_request)

    def test_initialize_then_tools_list(self):
        status, body, _ = self.post(_rpc("initialize"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"]["protocolVersion"],
                         mcp_server.PROTOCOL_VERSION)

        status, body, _ = self.post(_rpc("tools/list"))
        names = {t["name"] for t in json.loads(body)["result"]["tools"]}
        self.assertEqual(names, {t["name"] for t in mcp_server.TOOL_SCHEMAS})

    def test_notification_is_202_with_no_body(self):
        status, body, _ = self.post(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}).encode())
        self.assertEqual(status, 202)
        self.assertEqual(body, "")

    def test_tools_call_returns_structured_payload(self):
        status, body, _ = self.post(_rpc("tools/call", {
            "name": "sde_probe", "arguments": {"url": EXAMPLE_URL}}))
        result = json.loads(body)["result"]
        self.assertEqual(status, 200)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structured"]["provider_resolved"], "example")
        self.assertIn("example", result["content"][0]["text"])

    def test_run_status_tool_is_reachable(self):
        status, body, _ = self.post(_rpc("tools/call", {
            "name": "sde_run_status", "arguments": {"video_id": "7673343206544706837"}}))
        payload = json.loads(body)["result"]["structured"]
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], "run-status.v1")
        self.assertEqual(payload["run"]["status"], "no_run_found")

    def test_jsonrpc_error_is_200_with_error_object(self):
        status, body, _ = self.post(_rpc("resources/list"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32601)

    def test_malformed_json_is_a_parse_error_not_a_crash(self):
        status, body, _ = self.post(b"{not json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)
        self.assertEqual(self.post(_rpc("ping"))[0], 200)  # server still healthy

    def test_health_endpoint(self):
        status, body, headers = self.request("GET", "/health")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["auth"], "none")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class TestHardening(_ServerMixin, unittest.TestCase):
    def test_non_loopback_host_header_is_refused(self):
        """DNS rebinding: an attacker hostname pointing at 127.0.0.1 must fail."""
        status, body, _ = self.post(_rpc("ping"), headers={"Host": "evil.example:8765"})
        self.assertEqual(status, 400)
        self.assertIn("loopback", json.loads(body)["error"]["message"])
        # ...and the loopback names still work
        for host in ("127.0.0.1", "127.0.0.1:9999", "localhost:8765", "localhost"):
            status, _, _ = self.post(_rpc("ping"), headers={"Host": host})
            self.assertEqual(status, 200, host)

    def test_missing_host_header_is_refused(self):
        status, _, _ = self.post(_rpc("ping"), headers={"Host": ""})
        self.assertEqual(status, 400)

    def test_content_type_must_be_json(self):
        """`text/plain` is CORS-safelisted: refusing it blocks drive-by tool calls."""
        for content_type in ("text/plain", "application/x-www-form-urlencoded",
                             "text/plain;charset=UTF-8", ""):
            status, body, _ = self.post(_rpc("tools/call", {
                "name": "sde_list_providers", "arguments": {}}),
                headers={"Content-Type": content_type} if content_type else {"Content-Type": ""})
            self.assertIn(status, (415,), f"{content_type!r} should be refused, got {status}")
        status, _, _ = self.post(_rpc("ping"), headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_chunked_body_is_refused(self):
        status, body, _ = self.post(_rpc("ping"), headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(status, 413)
        self.assertIn("chunked", json.loads(body)["error"]["message"])

    def test_oversized_body_is_refused(self):
        """The cap is enforced from the header alone — the body is never read.

        Deliberately NOT posted through urllib: pushing 1 MiB races the refusal
        and the client can be left with BrokenPipe instead of the 413, which
        made this check flaky across machines. Announcing the length and
        sending nothing pins the guard deterministically.
        """
        raw = _raw_exchange(self.port, (
            f"POST /mcp HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {mcp_http.MAX_BODY_BYTES + 10}\r\n"
            f"\r\n"
        ).encode())
        self.assertEqual(_status_of(raw), 413)
        self.assertIn("melebihi", raw)

    def test_refusals_close_the_connection(self):
        """A refused request must not leave its body to poison the next one (V8).

        Every refusal returns before the body is read, and the transport is
        keep-alive; without an explicit close those bytes would be parsed as the
        next request on the same socket, letting a caller smuggle a tool call
        past the Host/Content-Type/size checks it just failed.
        """
        body = _rpc("tools/call", {"name": "sde_list_providers", "arguments": {}})
        heads = {
            # wrong Content-Type → 415 (the CORS-safelisted-type guard)
            415: ["POST /mcp HTTP/1.1", f"Host: 127.0.0.1:{self.port}",
                  "Content-Type: text/plain"],
            # non-loopback Host → 400 (DNS rebinding)
            400: ["POST /mcp HTTP/1.1", "Host: evil.example",
                  "Content-Type: application/json"],
        }
        for expected, lines in heads.items():
            with self.subTest(status=expected):
                head = "\r\n".join([*lines, f"Content-Length: {len(body)}", "", ""])
                raw = _raw_exchange(self.port, head.encode(), body)
                self.assertEqual(_status_of(raw), expected)
                self.assertIn("Connection: close", raw)

    def test_method_and_path_guards(self):
        status, _, headers = self.request("GET", "/mcp")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(self.request("OPTIONS", "/mcp")[0], 405)
        status, body, _ = self.post(_rpc("ping"), path="/nope")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], -32600)

    def test_reflected_path_is_truncated(self):
        status, body, _ = self.post(_rpc("ping"), path="/" + "a" * 5000)
        self.assertEqual(status, 404)
        self.assertLess(len(json.loads(body)["error"]["message"]), 400)

    def test_token_mode_requires_a_valid_bearer(self):
        status, body, _ = self.post(_rpc("ping"), port=self.token_port)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], -32001)
        self.assertEqual(self.post(_rpc("ping"), port=self.token_port,
                                   headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.post(_rpc("ping"), port=self.token_port,
                                   headers={"X-SDE-Token": "s3cret"})[0], 200)
        status, body, _ = self.post(_rpc("ping"), port=self.token_port,
                                    headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {})
        self.assertEqual(self.request("GET", "/health", port=self.token_port)[0], 200)

    def test_non_loopback_bind_needs_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            mcp_http.build_server(host="0.0.0.0", port=0)
        server = mcp_http.build_server(host="0.0.0.0", port=0, allow_remote=True, quiet=True)
        server.server_close()
        with self.assertRaises(ValueError):
            mcp_http.build_server(path="mcp", port=0)

    def test_socket_timeout_is_set(self):
        self.assertGreater(mcp_http.SOCKET_TIMEOUT_S, 0)
        self.assertLessEqual(mcp_http.MAX_BODY_BYTES, 8 * 1024 * 1024)

    def test_cli_defaults_are_loopback(self):
        args = mcp_http._parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)
        self.assertEqual(args.path, "/mcp")
        self.assertIsNone(args.token)
        self.assertFalse(args.allow_remote)

    def test_token_comes_from_env_and_the_flag_wins(self):
        # `--token` shows up in `ps`; the env var is the documented way in (TM-29).
        self.assertEqual(mcp_http._resolve_token(None, {"SDE_HTTP_TOKEN": "from-env"}),
                         "from-env")
        self.assertEqual(mcp_http._resolve_token("from-flag", {"SDE_HTTP_TOKEN": "from-env"}),
                         "from-flag")
        for empty in ({}, {"SDE_HTTP_TOKEN": ""}, {"SDE_HTTP_TOKEN": "   "}):
            self.assertIsNone(mcp_http._resolve_token(None, empty), empty)
        # A blank flag must not silently disable auth.
        with self.assertRaises(ValueError):
            mcp_http._resolve_token("  ", {"SDE_HTTP_TOKEN": "from-env"})

    def test_helper_accepts_only_loopback_hosts(self):
        for value in ("127.0.0.1:8765", "127.0.0.1", "localhost:1", "[::1]:8765"):
            self.assertTrue(mcp_http._host_is_loopback(value), value)
        for value in ("", "evil.example", "evil.example:80", "192.168.1.9:8765",
                      "0.0.0.0:8765", "[::]:8765"):
            self.assertFalse(mcp_http._host_is_loopback(value), value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
