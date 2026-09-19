#!/usr/bin/env python3
"""
Agent plugin discovery — external providers without touching core.

Sama seperti entry-point plugins di tool modern: taruh plugin di
`plugins/<name>/<name>.py` (atau di direktori lain via env `PLUGIN_PATHS`),
muat semuanya sebelum CLI jalan, dan CLI menerima `--plugin` / `--list-plugins`.

Kontrak plugin (per modul):
  - Harus memanggil `harness.register(name, pattern, adapter)` saat import.
  - Adapter harus subclass `src.providers.base.ProviderAdapter`.
  - URL regex-nya yang menentukan routing (first match wins, last-registered
    override — lihat `src/harness/registry.py`).

Plugin yang gagal import TIDAK membungkam run (TRANSPARENCY.md: empty success
hides a failure): namanya tercatat di `load_errors()` dan `--list-plugins`
menampilkannya, supaya author plugin tahu kenapa pluginnya tidak muncul.

Pemakaian:
    from src.plugins import load_plugins
    load_plugins()                      # idempotent — aman dipanggil berulang
    print(list_plugins())               # {name: pattern} + load errors
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLUGINS_DIR = ROOT / "plugins"
_env = os.environ.get("PLUGIN_PATHS", "").strip()
EXTRA_PLUGIN_DIRS = [Path(p).expanduser() for p in _env.split(":") if p.strip()]

# name → module; errors → (plugin_file, reason). Both introspectable.
_loaded: Dict[str, ModuleType] = {}
_errors: List[Dict[str, str]] = []
_attempted = False


def plugin_dirs() -> List[Path]:
    """Direktori plugin aktif: default repo + PLUGIN_PATHS (env)."""
    dirs = [DEFAULT_PLUGINS_DIR] + EXTRA_PLUGIN_DIRS
    return [d for d in dirs if d.is_dir()]


def _plugin_modules(d: Path) -> List[Path]:
    """`<dir>/<name>/<name>.py` dan `<dir>/<name>.py`, urut stabil."""
    out: List[Path] = []
    for sub in sorted(d.glob("*")):
        if sub.is_dir():
            mod = sub / f"{sub.name}.py"
            if mod.is_file():
                out.append(mod)
        elif sub.suffix == ".py":
            out.append(sub)
    return out


def _load_module(mod_file: Path) -> ModuleType:
    """Import satu file plugin sebagai modul `plugins.<stem>` (stable id)."""
    name = f"plugins.{mod_file.stem}"
    spec = importlib.util.spec_from_file_location(name, mod_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {mod_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # register BEFORE exec → self-imports resolve
    spec.loader.exec_module(module)
    return module


def load_plugins() -> Dict[str, ModuleType]:
    """Muat semua plugin dari semua direktori. Idempotent per proses.

    Kegagalan satu plugin tidak menghentikan plugin lain — masing-masing
    tercatat di `_errors`. Return: {plugin_name: module}.
    """
    global _attempted
    if _attempted:
        return dict(_loaded)
    _attempted = True

    from src.harness.registry import harness  # local import: no cycle

    for d in plugin_dirs():
        for mod_file in _plugin_modules(d):
            try:
                mod = _load_module(mod_file)
            except Exception as e:  # noqa: BLE001 — plugin author sees the reason
                _errors.append({
                    "plugin": str(mod_file.relative_to(ROOT))
                    if mod_file.is_relative_to(ROOT) else str(mod_file),
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            # Module imported successfully; whether it registered a provider is
            # visible via harness.providers() in list_plugins() — an import-only
            # helper module is allowed too.
            _loaded[mod_file.stem] = mod
    return dict(_loaded)


def list_plugins() -> Dict:
    """Introspeksi untuk CLI/--list-plugins: registered + errors + dirs."""
    from src.harness.registry import harness
    load_plugins()
    return {
        "dirs": [str(d) for d in plugin_dirs()],
        "registered": {n: (repr(a.__class__.__module__ + "." + a.__class__.__name__))
                       for n, a in harness.providers().items()},
        "loaded_plugin_modules": sorted(_loaded),
        "load_errors": list(_errors),
    }


def load_errors() -> List[Dict[str, str]]:
    load_plugins()
    return list(_errors)
