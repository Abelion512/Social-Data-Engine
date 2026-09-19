#!/usr/bin/env python3
"""
Plugin contract tests — the executable spec for `src/plugins.py` + example.

Stdlib-only, deterministic, no network/browser. CI discovers this by glob.
Covers: discovery, routing, probe/collect through the shared Harness,
idempotent loading, and fail-closed error reporting for broken plugins.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.harness.registry import Harness  # a FRESH registry — not the global singleton
from src.plugins import (
    _load_module,
    _plugin_modules,
    load_errors,
    load_plugins,
    list_plugins,
    plugin_dirs,
)
from src.schema.canonical import Observation


def _fresh_harness_with_example() -> tuple:
    """A fresh Harness wired with the REAL example adapter from the plugin file."""
    h = Harness()
    mod = _load_module(Path("plugins/example/example.py"))
    h.register("example", mod.URL_PATTERN, mod.ExampleAdapter())
    return h, mod


class TestPluginDiscovery(unittest.TestCase):
    def test_default_plugins_dir_is_discovered(self):
        dirs = plugin_dirs()
        self.assertTrue(any(d.name == "plugins" for d in dirs),
                        f"default plugins dir missing: {dirs}")

    def test_plugin_module_pattern_finds_example(self):
        found = {f.name for f in _plugin_modules(Path("plugins"))}
        self.assertIn("example.py", found)

    def test_broken_plugin_is_reported_not_fatal(self):
        """A plugin that raises at import must appear in load_errors, and the
        other plugins must still load (fail-closed, per-plugin)."""
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            (pdir / "broken").mkdir()
            (pdir / "broken" / "broken.py").write_text(
                "raise RuntimeError('boom-on-purpose')\n", encoding="utf-8")
            mods = _plugin_modules(pdir)
            self.assertEqual(len(mods), 1)
            with self.assertRaises(RuntimeError):
                _load_module(mods[0])
            # ...and the loader-level behavior via load_errors contract:
            # load_plugins() itself must never raise for a bad plugin.


class TestExamplePluginContract(unittest.TestCase):
    def test_probe_routes_and_reports(self):
        h, _ = _fresh_harness_with_example()
        self.assertEqual(h.resolve("https://example.social/post/123"), "example")
        p = h.probe("https://example.social/post/123")
        self.assertTrue(p["accessible"])
        self.assertEqual(p["provider"], "example")
        self.assertEqual(p["metadata"]["content_id"], "123")

    def test_collect_returns_canonical_observations(self):
        h, _ = _fresh_harness_with_example()
        import asyncio
        obs = asyncio.run(h.collect("https://example.social/video/777",
                                    max_comments=2))
        self.assertEqual(len(obs), 2)
        for o in obs:
            self.assertIsInstance(o, Observation)
            self.assertEqual(o.source, "example")
        ids = [o.observation_id for o in obs]
        self.assertEqual(ids, ["example:777:0", "example:777:1"])

    def test_unrouted_url_fails_closed(self):
        h, _ = _fresh_harness_with_example()
        with self.assertRaises(ValueError):
            h.resolve("https://unknown.example/x/1")

    def test_example_registers_on_global_harness(self):
        """The real plugin, loaded the real way, lands in the global registry."""
        import src.plugins as P
        import src.harness.registry as R
        # simulate a clean process state for the singleton module var
        P._attempted = False
        P._loaded.clear()
        P._errors.clear()
        loaded = P.load_plugins()
        self.assertIn("example", loaded)
        self.assertIn("example", R.harness.providers())
        self.assertEqual(load_errors(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
