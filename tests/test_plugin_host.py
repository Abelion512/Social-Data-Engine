#!/usr/bin/env python3
"""
Host-plugin contract — the executable spec for `integrations/plugin/`.

The plugin package is deliberately host-agnostic: a manifest that declares how to
run the engine, a generic JS adapter, and an installer that writes the resolved
runtime paths. These checks pin exactly that contract, so a drift fails CI
instead of failing inside whatever host (or shell) consumes the plugin:

  - `manifest.json` declares transports only — the TOOL list is never duplicated
    (single source: `src/mcp_server.TOOL_SCHEMAS` via `tools/list`)
  - the JS adapter imports `node:` builtins only, never spawns a shell, and its
    convenience `handlers` map matches the real tool names
  - `install.sh` writes `plugin.runtime.json` through the interpreter's
    `json.dump` (no string interpolation ⇒ no path can corrupt or extend the
    config) and refuses paths a JSON file cannot represent
  - the plugin folder is invisible to this repo's own `src/plugins.py` loader

Stdlib only, deterministic, no network. The node-based end-to-end check
self-skips when node is not installed.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mcp_server import PROTOCOL_VERSION, TOOL_SCHEMAS  # noqa: E402
from src.plugins import _plugin_modules  # noqa: E402

PLUGIN_DIR = ROOT / "integrations" / "plugin"
EXAMPLE_URL = "https://example.social/post/123"
KEBAB = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _manifest() -> dict:
    return json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))


def _index_js() -> str:
    return (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")


def _install_sh() -> str:
    return (PLUGIN_DIR / "install.sh").read_text(encoding="utf-8")


class TestPluginManifest(unittest.TestCase):
    def test_identity_and_schema_stamp(self):
        m = _manifest()
        self.assertEqual(m["schema_version"], "plugin.v1")
        self.assertRegex(m["name"], KEBAB)
        self.assertRegex(m["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(m["description"].strip())

    def test_does_not_duplicate_the_tool_list(self):
        """Tools come from `tools/list`; a copy here would drift silently."""
        raw = (PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn('"tools"', raw)
        self.assertEqual(_manifest()["tools_source"], "mcp:tools/list")
        for tool in TOOL_SCHEMAS:
            self.assertNotIn(tool["name"], raw, f"manifest hardcodes {tool['name']}")

    def test_transports_point_at_real_entrypoints(self):
        m = _manifest()
        stdio = m["transports"]["stdio_mcp"]
        self.assertEqual(stdio["command"][1:], ["-m", "src.mcp_server"])
        self.assertEqual(stdio["command"][0], "{python}")
        self.assertEqual(stdio["cwd"], "{repo_root}")
        self.assertEqual(stdio["framing"], "json-lines")
        http = m["transports"]["http_mcp"]
        self.assertEqual(http["command"][1:3], ["-m", "src.mcp_http"])
        self.assertTrue(http["url"].startswith("http://127.0.0.1:"), http["url"])
        # both modules must actually exist in this repo
        for module in ("src/mcp_server.py", "src/mcp_http.py"):
            self.assertTrue((ROOT / module).is_file(), module)

    def test_declares_read_only_use(self):
        requires = _manifest()["requires"]
        self.assertEqual(requires["credentials"], "none")
        self.assertEqual(requires["network"], "none")
        self.assertIn("access_mode", _manifest())


class TestPluginAdapter(unittest.TestCase):
    def _code_lines(self) -> str:
        """index.js without block/line comments (docstrings mention `require('./index.js')`)."""
        kept = []
        for line in _index_js().splitlines():
            stripped = line.strip()
            if stripped.startswith(('*', '//', '/*')):
                continue
            kept.append(line)
        return "\n".join(kept)

    def test_node_builtins_only_and_never_a_shell(self):
        js = self._code_lines()
        requires = re.findall(r"require\(\s*'([^']+)'\s*\)", js)
        self.assertTrue(requires)
        for module in requires:
            self.assertTrue(module.startswith("node:"), f"unexpected dependency: {module}")
        self.assertNotIn("shell: true", js)
        self.assertNotRegex(js, r"\bexec\(\s*[`'\"]")
        self.assertIn("execFile", js)

    def test_exposes_the_generic_entry_points(self):
        js = _index_js()
        exports = re.search(r"module\.exports = \{([^}]*)\}", js)
        self.assertIsNotNone(exports, "module.exports not found")
        exported = {name.strip() for name in exports.group(1).split(",")}
        self.assertLessEqual(
            {"manifest", "listTools", "invoke", "invokeText", "handlers", "main"},
            exported,
            f"adapter must export its generic entry points, got {sorted(exported)}",
        )
        self.assertIn("require.main === module", js)  # runnable CLI

    def test_handler_map_matches_the_real_tool_names(self):
        js = _index_js()
        match = re.search(r"for \(const tool of \[(.*?)\]\)", js, re.S)
        self.assertIsNotNone(match, "handlers map not found in index.js")
        names = set(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(names, {t["name"] for t in TOOL_SCHEMAS})

    def test_output_is_bounded(self):
        js = _index_js()
        self.assertIn("MAX_TEXT", js)
        self.assertIn("MAX_OUTPUT_BYTES", js)
        self.assertIn("MAX_CONFIG_BYTES", js)

    def test_repo_loader_ignores_the_host_plugin_folder(self):
        self.assertEqual(_plugin_modules(PLUGIN_DIR), [])

    def test_node_end_to_end_when_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH — adapter not executed here")
        env = {"SDE_ROOT": str(ROOT), "SDE_PYTHON": sys.executable, "PATH": "/usr/bin:/bin"}
        listing = subprocess.run([node, str(PLUGIN_DIR / "index.js"), "--list-tools"],
                                 capture_output=True, text=True, timeout=120,
                                 env=env, cwd=str(ROOT))
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertEqual({t["name"] for t in json.loads(listing.stdout)},
                         {t["name"] for t in TOOL_SCHEMAS})

        call = subprocess.run([node, str(PLUGIN_DIR / "index.js"), "sde_probe",
                               json.dumps({"url": EXAMPLE_URL})],
                              capture_output=True, text=True, timeout=120,
                              env=env, cwd=str(ROOT))
        self.assertEqual(call.returncode, 0, call.stderr)
        payload = json.loads(call.stdout)
        self.assertEqual(payload["provider_resolved"], "example")
        self.assertTrue(payload["accessible"])

    def test_node_reports_tool_errors_instead_of_an_empty_success(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        env = {"SDE_ROOT": str(ROOT), "SDE_PYTHON": sys.executable, "PATH": "/usr/bin:/bin"}
        proc = subprocess.run([node, str(PLUGIN_DIR / "index.js"), "sde_probe", "{}"],
                              capture_output=True, text=True, timeout=120,
                              env=env, cwd=str(ROOT))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("url", proc.stderr.lower())


class TestPluginInstaller(unittest.TestCase):
    def test_requires_an_explicit_target_directory(self):
        proc = subprocess.run(["bash", str(PLUGIN_DIR / "install.sh")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--dir", proc.stderr)

    def test_has_no_sudo_and_writes_config_through_python(self):
        script = _install_sh()
        self.assertNotIn("sudo", script)
        # json.dump (interpreter) instead of shell string interpolation
        self.assertIn("json.dumps", script)
        self.assertNotIn('"repo_root": "$(', script)
        self.assertIn("--dry-run", script)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plugins" / "sde"
            proc = subprocess.run(["bash", str(PLUGIN_DIR / "install.sh"),
                                   "--dir", str(target), "--python", sys.executable,
                                   "--dry-run"],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("dry-run", proc.stdout)
            self.assertFalse(target.exists())

    def test_install_and_config_survive_a_hostile_path(self):
        """A quote/backslash in the repo path must not corrupt the config file
        (invalid JSON there would hand the plugin a different interpreter)."""
        with tempfile.TemporaryDirectory() as tmp:
            weird = Path(tmp) / 'od"d\\back'
            fake_repo = weird / "sde"
            (fake_repo / "src").mkdir(parents=True)
            (fake_repo / "src" / "__init__.py").write_text("", encoding="utf-8")
            (fake_repo / "src" / "mcp_server.py").write_text("", encoding="utf-8")
            plugin_copy = fake_repo / "integrations" / "plugin"
            shutil.copytree(PLUGIN_DIR, plugin_copy)
            target = weird / "host plugins" / "sde"

            proc = subprocess.run(["bash", str(plugin_copy / "install.sh"),
                                   "--dir", str(target), "--python", sys.executable],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            config = json.loads((target / "plugin.runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(config["repo_root"], str(fake_repo))
            self.assertEqual(config["python"], sys.executable)
            self.assertEqual(set(config), {"repo_root", "python", "plugin_version"})
            self.assertTrue((target / "index.js").is_file())
            self.assertTrue((target / "manifest.json").is_file())

    def test_refuses_a_path_a_json_file_cannot_represent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad\nnewline"
            target.mkdir()
            proc = subprocess.run(["bash", str(PLUGIN_DIR / "install.sh"),
                                   "--dir", str(target), "--python", sys.executable],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("newline", proc.stderr)

    def test_unknown_flag_is_rejected(self):
        proc = subprocess.run(["bash", str(PLUGIN_DIR / "install.sh"), "--nope"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
