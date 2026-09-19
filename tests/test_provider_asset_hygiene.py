#!/usr/bin/env python3
"""
Regression tests for provider + asset hygiene (stdlib only, deterministic,
no browser / no network). Covers the fixes from the 2026-09-19 codebase tidy:

  1. `LinkedInAdapter._scrape_comments` no longer raises `NameError: env`
     (it referenced an undefined global). It returns [] and *announces* why,
     so an empty result stays distinguishable from a suppressed failure
     (policies/TRANSPARENCY.md).
  2. Exported session-cookie files are written owner-only (0600, dir 0700) via
     `src.runtime.context.write_private_text` — TM-19 asset handling.
  3. The legacy single-file pipeline is a real submodule of the package
     (`src/pipeline/legacy.py`) instead of a shadowed `src/pipeline.py` loaded
     through importlib; the package keeps re-exporting its public surface.
  4. The MARK exporter exists exactly once (`src/export/mark.py`); the duplicate
     `src/mark_export.py` is gone.

Run:  python tests/test_provider_asset_hygiene.py
      python -m pytest tests/test_provider_asset_hygiene.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_POST_URL = "https://www.linkedin.com/posts/some-slug-activity-7123456789012345678-AbCd"


def test_linkedin_collect_is_empty_but_not_silent():
    """No session configured → [] and an explicit stdout reason (no NameError)."""
    from src.providers.linkedin import LinkedInAdapter

    adapter = LinkedInAdapter()
    os.environ.pop("".join(["LINKED", "IN_", "USER", "NAME"]), None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        obs = asyncio.run(adapter.collect(_POST_URL))

    assert obs == [], f"expected no observations, got {obs!r}"
    out = buf.getvalue()
    assert "[linkedin]" in out, f"reason not announced on stdout: {out!r}"
    assert "returning 0 observations" in out


def test_linkedin_probe_parses_activity_id():
    from src.providers.linkedin import LinkedInAdapter

    probe = LinkedInAdapter().probe(_POST_URL)
    assert probe["provider"] == "linkedin"
    assert probe["accessible"] is True
    assert probe["metadata"]["post_id"] == "7123456789012345678"

    # Non-post URL → not accessible, but still no exception.
    assert LinkedInAdapter().probe("https://www.linkedin.com/feed/")["accessible"] is False


def test_exported_cookies_are_owner_only():
    """TM-19: a cookie export is 0600 in a 0700 directory, even on re-write."""
    from src.runtime.context import write_private_text

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "nested" / "tiktok-cookies.json"
        write_private_text(target, '{"sessionid": "redacted"}')

        file_mode = stat.S_IMODE(os.stat(target).st_mode)
        dir_mode = stat.S_IMODE(os.stat(target.parent).st_mode)
        assert file_mode == 0o600, f"cookie file mode {oct(file_mode)} != 0o600"
        assert dir_mode == 0o700, f"cookie dir mode {oct(dir_mode)} != 0o700"
        assert target.read_text() == '{"sessionid": "redacted"}'

        # A previously loosened file is tightened again, never reused as-is.
        os.chmod(target, 0o644)
        write_private_text(target, "{}")
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_pipeline_legacy_module_replaces_shadowed_file():
    from src import pipeline

    # 1. legacy code is a real submodule of the package (no importlib shadowing)
    assert Path(pipeline.legacy.__file__).name == "legacy.py"
    assert not (ROOT / "src" / "pipeline.py").exists(), \
        "src/pipeline.py still shadows the package — legacy move incomplete"

    # 2. public legacy surface stays reachable through the package
    for name in ("RAW_DIR", "CURATED_DIR", "NORM_DIR", "ENRICH_DIR", "REJECTED_DIR",
                 "MANIFEST_DIR", "PIPELINE_VERSION", "STAGES", "today_stamp",
                 "run_video", "stage_normalize", "stage_dedup", "stage_enrich",
                 "stage_quality_gate", "generate_manifest",
                 "quality_score", "llm_enrich_identities", "GATING_THRESHOLD"):
        assert hasattr(pipeline, name), f"src.pipeline lost legacy symbol {name}"
    assert callable(pipeline.run_video)
    assert str(pipeline.RAW_DIR).endswith(os.path.join("data", "raw"))

    # 3. modular sub-package still exposed
    for name in ("dedup", "quality", "identity", "stages", "improve"):
        assert hasattr(pipeline, name), f"src.pipeline lost module {name}"

    # 4. PEP 562 fallback: legacy-only symbols resolve without explicit re-export
    assert pipeline.hash_normalized("A  b") == pipeline.legacy.hash_normalized("A  b")

    # 5. unknown attributes still fail closed with a named error
    try:
        pipeline.definitely_not_a_symbol  # noqa: B018
    except AttributeError as exc:
        assert "definitely_not_a_symbol" in str(exc)
    else:
        raise AssertionError("unknown attribute should raise AttributeError")


def test_mark_exporter_exists_once():
    from src.export.mark import export_all, export_video  # noqa: F401

    assert not (ROOT / "src" / "mark_export.py").exists(), \
        "duplicate src/mark_export.py came back — src/export/mark.py is canonical"
    assert (ROOT / "src" / "export" / "mark.py").exists()


if __name__ == "__main__":
    tests = [
        test_linkedin_collect_is_empty_but_not_silent,
        test_linkedin_probe_parses_activity_id,
        test_exported_cookies_are_owner_only,
        test_pipeline_legacy_module_replaces_shadowed_file,
        test_mark_exporter_exists_once,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__} ✅")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__} ❌ : {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__} ❌ : {type(e).__name__}: {e}")
            failed += 1
    print(f"\nProvider/Asset Hygiene Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
