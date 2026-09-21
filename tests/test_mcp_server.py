#!/usr/bin/env python3
"""
MCP server contract tests — the executable spec for `src/mcp_server.py`.

Stdlib-only, deterministic, no browser/network (the example plugin's collect
is synthetic). Covers the JSON-RPC surface: initialize handshake, tools/list,
tools/call happy paths, per-request fail-closed errors, notifications, and a
full serve() round-trip through real stdin/stdout.
"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.mcp_server import (  # noqa: E402
    PROTOCOL_VERSION,
    SERVER_NAME,
    TOOL_SCHEMAS,
    handle_request,
    serve,
)

EXAMPLE_URL = "https://example.social/post/123"


def rpc(method, params=None, rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}


class TestMcpProtocol(unittest.TestCase):
    def test_initialize_handshake(self):
        r = handle_request(rpc("initialize"))
        self.assertEqual(r["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(r["result"]["serverInfo"]["name"], SERVER_NAME)
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list_exposes_the_tool_surface(self):
        r = handle_request(rpc("tools/list"))
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, {"sde_list_providers", "sde_probe", "sde_collect",
                                 "sde_run_status"})
        for t in r["result"]["tools"]:
            self.assertIn("inputSchema", t)
            self.assertTrue(t["description"].strip(), t["name"])

    def test_run_status_tool_is_read_only_and_validates_ids(self):
        r = handle_request(rpc("tools/call", {"name": "sde_run_status",
                                              "arguments": {"video_id": "1234567890"}}))
        payload = r["result"]["structured"]
        self.assertEqual(payload["schema_version"], "run-status.v1")
        self.assertEqual(payload["run"]["status"], "no_run_found")
        # hostile id → explicit per-request error, not a fabricated empty result
        bad = handle_request(rpc("tools/call", {"name": "sde_run_status",
                                                "arguments": {"video_id": "../../etc/passwd"}}))
        self.assertEqual(bad["error"]["code"], -32603)
        self.assertIn("slug", bad["error"]["message"])
        bad_args = handle_request(rpc("tools/call", {"name": "sde_run_status",
                                                     "arguments": {"max_runs": 0}}))
        self.assertEqual(bad_args["error"]["code"], -32603)

    def test_tools_call_list_providers(self):
        r = handle_request(rpc("tools/call", {"name": "sde_list_providers"}))
        self.assertFalse(r["result"]["isError"])
        payload = r["result"]["structured"]
        self.assertIn("tiktok", payload["providers"])
        self.assertIn("example", payload["providers"])
        self.assertEqual(payload["load_errors"], [])
        # MCP ContentBlock shape for real hosts:
        self.assertEqual(r["result"]["content"][0]["type"], "text")
        self.assertIn("example", r["result"]["content"][0]["text"])

    def test_tools_call_probe(self):
        r = handle_request(rpc("tools/call", {"name": "sde_probe",
                                              "arguments": {"url": EXAMPLE_URL}}))
        payload = r["result"]["structured"]
        self.assertEqual(payload["provider_resolved"], "example")
        self.assertTrue(payload["accessible"])
        self.assertEqual(payload["metadata"]["content_id"], "123")

    def test_tools_call_collect_bounded(self):
        r = handle_request(rpc("tools/call",
                               {"name": "sde_collect",
                                "arguments": {"url": EXAMPLE_URL, "max_items": 2}}))
        payload = r["result"]["structured"]
        self.assertEqual(payload["provider"], "example")
        self.assertEqual(payload["count"], 2)
        self.assertFalse(payload["truncated"])
        for obs in payload["observations"]:
            self.assertEqual(obs["source"], "example")  # canonical schema

    def test_unknown_tool_is_jsonrpc_error_not_crash(self):
        r = handle_request(rpc("tools/call", {"name": "nope"}))
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("available", r["error"]["data"])

    def test_unknown_method_is_jsonrpc_error(self):
        r = handle_request(rpc("resources/list"))
        self.assertEqual(r["error"]["code"], -32601)

    def test_tool_runtime_error_fails_closed_per_request(self):
        with mock.patch("src.mcp_server._tool_collect",
                        side_effect=RuntimeError("boom")):
            r = handle_request(rpc("tools/call",
                                   {"name": "sde_collect",
                                    "arguments": {"url": EXAMPLE_URL}}))
        self.assertEqual(r["error"]["code"], -32603)
        self.assertIn("boom", r["error"]["message"])

    def test_bad_url_rejected_before_routing(self):
        r = handle_request(rpc("tools/call", {"name": "sde_probe",
                                              "arguments": {"url": ""}}))
        self.assertEqual(r["error"]["code"], -32603)
        self.assertIn("url", r["error"]["message"])

    def test_url_with_control_characters_is_rejected(self):
        for nasty in ("https://example.social/post/1\nX", "https://x\r\ny", "a\x00b"):
            r = handle_request(rpc("tools/call", {"name": "sde_probe",
                                                  "arguments": {"url": nasty}}))
            self.assertEqual(r["error"]["code"], -32603, nasty)
            self.assertIn("kontrol", r["error"]["message"])

    def test_notification_gets_no_response(self):
        req = {"jsonrpc": "2.0", "method": "ping"}  # no id → notification
        self.assertEqual(handle_request(req), {})

    def test_ping(self):
        r = handle_request(rpc("ping"))
        self.assertEqual(r["result"], {})


class TestServeLoop(unittest.TestCase):
    def _serve(self, lines):
        stdin = io.StringIO("".join(l + "\n" for l in lines))
        out = io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", out):
            serve()
        return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]

    def test_full_stdio_roundtrip(self):
        resps = self._serve([
            json.dumps(rpc("initialize")),
            json.dumps(rpc("tools/list", rid=2)),
            json.dumps(rpc("tools/call",
                           {"name": "sde_collect",
                            "arguments": {"url": EXAMPLE_URL, "max_items": 1}},
                           rid=3)),
            "not json at all",                        # parse error → id null
            json.dumps(rpc("tools/call", {"name": "nope"}, rid=5)),  # tool error
            json.dumps({"jsonrpc": "2.0", "method": "ping"}),        # notification
        ])
        self.assertEqual(len(resps), 5)  # notification produces NO response line
        self.assertEqual(resps[0]["id"], 1)
        self.assertEqual(resps[0]["result"]["serverInfo"]["name"], SERVER_NAME)
        self.assertEqual(len(resps[1]["result"]["tools"]), len(TOOL_SCHEMAS))
        self.assertEqual(resps[2]["result"]["structured"]["count"], 1)
        self.assertIsNone(resps[3]["id"])
        self.assertEqual(resps[3]["error"]["code"], -32700)
        self.assertEqual(resps[4]["error"]["code"], -32602)
        # A broken request earlier must not have stopped the server: rid=5 ran.


if __name__ == "__main__":
    unittest.main(verbosity=2)
