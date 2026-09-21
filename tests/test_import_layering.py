#!/usr/bin/env python3
"""
Import layering — the executable gate for two invariants that were only
documented before (and were silently violated by the import graph):

1. **Provider-blind runtime** (NFR-007, SDD §3): nothing under `src/runtime/`
   imports providers, the browser harness, the collector or the browser
   selector — statically, at module level.
2. **The host path stays light**: importing the MCP surfaces
   (`src.mcp_server`, `src.mcp_http`) must not drag the browser stack
   (`src.collector` → `src.browser_selector` → `playwright`/`camoufox`/
   `urllib.request`/`asyncio`). That is both a performance property (the
   process is spawned per tool call by a plugin host) and a robustness one: in
   an environment where the optional browser packages are absent, listing tools
   and probing a URL must still work.

The lazy re-exports that keep the old import paths working are asserted too, so
"make it lazy" can never quietly become "make it unavailable".

Deterministic, stdlib only, offline (each check runs in a fresh interpreter so
one import cannot hide another module's graph).
"""
import ast
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BROWSER_STACK = ("src.collector", "src.browser_selector", "playwright", "camoufox",
                 "urllib.request", "asyncio")
FORBIDDEN_RUNTIME_IMPORTS = ("src.providers", "src.harness", "src.collector",
                             "src.browser_selector")


def _run(code: str) -> str:
    """Run `code` in a fresh interpreter (repo root as cwd) and return stdout."""
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    if proc.returncode != 0:
        raise AssertionError(f"fresh-interpreter check failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


class TestRuntimeIsProviderBlind(unittest.TestCase):
    def test_runtime_modules_never_import_providers_or_browser_layers(self):
        offenders = []
        runtime_dir = ROOT / "src" / "runtime"
        self.assertTrue(runtime_dir.is_dir())
        for path in sorted(runtime_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:  # module level only; function-level is deferred by design
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    if name.startswith(FORBIDDEN_RUNTIME_IMPORTS):
                        offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [], f"runtime must stay provider-blind: {offenders}")

    def test_importing_the_whole_runtime_loads_no_provider_or_browser_module(self):
        out = _run("""
            import importlib, pkgutil, sys
            import src.runtime
            for mod in pkgutil.iter_modules(src.runtime.__path__, 'src.runtime.'):
                importlib.import_module(mod.name)
            leaked = sorted(m for m in sys.modules
                            if m.startswith(('src.providers', 'src.harness', 'src.collector',
                                             'src.browser_selector')))
            print('LEAKED:' + ','.join(leaked))
        """)
        self.assertTrue(out.strip().startswith("LEAKED:"), out)
        leaked = out.strip().split("LEAKED:")[1]
        self.assertEqual(leaked, "", f"runtime pulled in {leaked}")


class TestHostPathStaysLight(unittest.TestCase):
    def test_importing_the_stdio_server_loads_no_browser_stack(self):
        out = _run(f"""
            import sys, src.mcp_server
            heavy = [m for m in {BROWSER_STACK!r} if m in sys.modules]
            print('HEAVY:' + ','.join(heavy))
        """)
        self.assertEqual(out.strip().split("HEAVY:")[1], "",
                         f"host import path pulled {out.strip()}")

    def test_importing_the_http_server_loads_no_browser_stack(self):
        out = _run("""
            import sys, src.mcp_http
            heavy = [m for m in ('src.collector', 'src.browser_selector', 'playwright',
                                 'camoufox') if m in sys.modules]
            print('HEAVY:' + ','.join(heavy))
        """)
        self.assertEqual(out.strip().split("HEAVY:")[1], "", out.strip())

    def test_the_pipeline_module_stays_provider_free(self):
        out = _run("""
            import sys, src.pipeline.canonical_runner
            leaked = sorted(m for m in sys.modules if m.startswith('src.providers'))
            print('LEAKED:' + ','.join(leaked))
        """)
        self.assertEqual(out.strip().split("LEAKED:")[1], "", out.strip())

    def test_tool_listing_works_without_the_browser_packages(self):
        """`playwright` is absent in CI; tools/list must not need it."""
        out = _run("""
            import json, sys
            from src.mcp_server import handle_request
            resp = handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
            print(json.dumps({'tools': [t['name'] for t in resp['result']['tools']],
                              'playwright_loaded': 'playwright' in sys.modules,
                              'camoufox_loaded': 'camoufox' in sys.modules}))
        """)
        import json as _json
        payload = _json.loads(out.strip().splitlines()[-1])
        self.assertGreaterEqual(len(payload["tools"]), 4)
        self.assertFalse(payload["playwright_loaded"])
        self.assertFalse(payload["camoufox_loaded"])


class TestLazyReexportsStillResolve(unittest.TestCase):
    """Making imports lazy must not make the old import paths unavailable."""

    def test_legacy_attribute_paths_resolve(self):
        out = _run("""
            from src.providers.base import AgentTool
            from src.harness import BrowserAgent, AgentTool as AT2, ProviderAdapter
            from src.harness import agent as agent_mod, tools as tools_mod, human as human_mod
            import src.harness.registry as registry
            print('OK:' + ','.join([AgentTool.__name__, AT2.__name__, BrowserAgent.__name__,
                                    ProviderAdapter.__name__, agent_mod.__name__,
                                    tools_mod.__name__, human_mod.__name__, registry.__name__]))
        """)
        self.assertIn("BrowserAgent", out)
        self.assertIn("src.harness.registry", out)

    def test_collector_js_snippets_are_reachable_on_demand(self):
        out = _run("""
            import src.harness.tools as t
            js = t._collector_symbol('DOM_SCRAPE_JS')
            legacy = t.DOM_SCRAPE_JS  # module __getattr__ path
            print('OK:%d:%d' % (len(js or ''), len(legacy or '')))
        """)
        length, legacy_length = out.strip().split("OK:")[1].split(":")
        self.assertGreater(int(length), 0)
        self.assertGreater(int(legacy_length), 0)

    def test_unknown_attribute_still_raises_attributeerror(self):
        out = _run("""
            import src.harness, src.harness.tools, src.providers.base
            results = []
            for module in (src.harness, src.harness.tools, src.providers.base):
                try:
                    module.definitely_not_a_symbol
                    results.append('LEAK')
                except AttributeError:
                    results.append('ok')
            print('RESULT:' + ','.join(results))
        """)
        self.assertEqual(out.strip().split("RESULT:")[1], "ok,ok,ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
