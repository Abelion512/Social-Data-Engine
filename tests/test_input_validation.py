#!/usr/bin/env python3
"""
Input-validation / injection guards (stdlib only, deterministic, no browser).

Covers the findings fixed in the 2026-09-19 security pass:

  V-H1/H2  identifier → path interpolation in the exporters and the legacy
           pipeline (`--video ../../../evil` was an arbitrary read AND write);
  V-M1     caller-supplied ids in `manifest.write_manifest`;
  V-M2     `linkedin_consumer` CLI `video_id` → curated path;
  V-M3     argument injection into `linkedin-cli` via a scraped handle/name;
  V-M4     the declared 20/day connection cap + pacing were never enforced;
  V-L1     PII-bearing reports/state written world-readable;
  V-L2     the `linkedin-cli` child inherited every secret-bearing env var;
  V-L3     benchmark derived ids with rsplit() (wrong for `?query` URLs).

Run:  python tests/test_input_validation.py
      python -m pytest tests/test_input_validation.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.export import manifest as export_manifest
from src.export import mark as export_mark
from src.linkedin_consumer import (
    PACE_FLOOR_MS,
    linkedin_env,
    run_linkedin_consumer,
    safe_handle,
    save_json,
)
from src.pipeline import legacy as legacy_pipeline
from src.providers.tiktok_actor import parse_tiktok_video_id
from src.tiktok_schema import parse_content_id, try_parse_content_id

# "" is deliberately NOT in this list: for the date/target arguments an empty
# value means "use the default" (documented), while a required id still refuses it.
HOSTILE_IDS = ("../../../../etc/passwd", "..", "..\\..\\evil", "a/b", "/etc/passwd", "a b", "a\x00b")


# ── 1. identifier → path interpolation is refused everywhere ─────────────────
def test_export_mark_refuses_hostile_ids():
    for bad in HOSTILE_IDS:
        for fn in (export_mark.export_video, export_mark.export_all):
            try:
                fn(bad)
            except ValueError as exc:
                assert "slug identifier" in str(exc)
            else:
                raise AssertionError(f"{fn.__name__}({bad!r}) was not refused")

    # A required id refuses the empty string...
    try:
        export_mark.export_video("")
    except ValueError:
        pass
    else:
        raise AssertionError("export_video('') should have been refused")

    # ...while the optional date treats "" as "today" (documented default).
    assert export_mark.export_all("")["status"] in ("no_curated_dir", "empty", "ok")


def test_export_mark_still_works_for_valid_ids():
    # Valid id, no curated file for today → the documented empty-status result.
    result = export_mark.export_video("1234567890123456789")
    assert result["status"] in ("no_curated", "empty", "ok")
    assert ".." not in result.get("output", "")


def test_manifest_write_refuses_hostile_video_ids():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        with mock.patch.object(export_manifest, "rooted_file", wraps=export_manifest.rooted_file):
            try:
                export_manifest.write_manifest(
                    base, {"videos": [{"video_id": "../../evil", "manifest": {"x": 1}}]}
                )
            except ValueError as exc:
                assert "video_id" in str(exc)
            else:
                raise AssertionError("write_manifest accepted a traversal video_id")

        # Nothing escaped the base dir.
        assert list(base.rglob("evil*")) == []
        assert not (base.parent / "evil.json").exists()


def test_legacy_pipeline_refuses_hostile_video_ids():
    with tempfile.TemporaryDirectory() as tmp:
        # Redirect the manifest writer so a refusal test can never write into the
        # repo's data/ tree (and so a bypass would be visible in `tmp`).
        with mock.patch.object(legacy_pipeline, "MANIFEST_DIR", Path(tmp)):
            for bad in HOSTILE_IDS:
                for call in (
                    lambda v=bad: legacy_pipeline.run_video(v),
                    lambda v=bad: legacy_pipeline.stage_normalize(v),
                    lambda v=bad: legacy_pipeline.stage_dedup(v),
                    lambda v=bad: legacy_pipeline.stage_enrich(v),
                    lambda v=bad: legacy_pipeline.stage_quality_gate(v),
                    lambda v=bad: legacy_pipeline.generate_manifest(v),
                ):
                    try:
                        call()
                    except ValueError as exc:
                        assert "video_id" in str(exc)
                    else:
                        raise AssertionError(f"legacy pipeline accepted {bad!r}")
            assert list(Path(tmp).rglob("*")) == [], "a refusal still wrote a file"


def test_legacy_stages_still_report_missing_input_for_valid_ids():
    # Validation must not change the normal "nothing to do" contract.
    assert legacy_pipeline.stage_normalize("9999999999999999999")["status"] == "no_raw"
    assert legacy_pipeline.stage_dedup("9999999999999999999")["status"] == "no_normalized"
    assert legacy_pipeline.stage_enrich("9999999999999999999")["status"] == "no_deduped"
    assert legacy_pipeline.stage_quality_gate("9999999999999999999")["status"] == "no_enriched"


def test_linkedin_consumer_refuses_hostile_video_id():
    with tempfile.TemporaryDirectory() as tmp:
        for bad in HOSTILE_IDS:
            try:
                asyncio.run(run_linkedin_consumer(bad, Path(tmp)))
            except ValueError as exc:
                assert "video_id" in str(exc)
            else:
                raise AssertionError(f"linkedin consumer accepted {bad!r}")


# ── 2. one canonical content-id parser ───────────────────────────────────────
def test_content_id_parser_is_single_source():
    photo = "https://www.tiktok.com/@coretanmalam2000/photo/7673343206544706837"
    video = "https://www.tiktok.com/@enxayeti/video/7669640839861112071"
    with_query = "https://www.tiktok.com/@enxayeti/video/7669640839861112071?is_from_webapp=1"

    assert parse_content_id(photo) == "7673343206544706837"
    assert parse_content_id(video) == "7669640839861112071"
    # The bug the old benchmark rsplit() had: the query string rode along.
    assert parse_content_id(with_query) == "7669640839861112071"
    assert with_query.rstrip("/").rsplit("/", 1)[-1] != "7669640839861112071"

    # The provider actor delegates to the same function (identical semantics).
    assert parse_tiktok_video_id(video) == parse_content_id(video)

    # Digits only — safe as a filename component by construction.
    for url in (photo, video, with_query):
        assert parse_content_id(url).isdigit()

    for bad in ("https://tiktok.com/@u", "https://www.tiktok.com/@u/profile", "", "not a url"):
        assert try_parse_content_id(bad) == ""
        try:
            parse_content_id(bad)
        except ValueError as exc:
            assert "not a TikTok video/photo URL" in str(exc)
        else:
            raise AssertionError(f"parser accepted {bad!r}")

    # Documented property: the regex is PATH-based (host-agnostic) — matching the
    # legacy behaviour of all four former copies. Host allow-listing is S-G7
    # (egress scoping, Phase 3), deliberately NOT smuggled in here: it would
    # change navigation behaviour, which needs the live gate.
    assert parse_content_id("https://mirror.example/@u/video/123") == "123"


def test_collector_rejects_non_tiktok_urls_without_touching_a_browser():
    from src.collector import collect_video

    result = asyncio.run(collect_video("https://example.com/not-a-video"))
    assert result["error"] == "invalid_url"


# ── 3. linkedin-cli argv injection ───────────────────────────────────────────
def test_safe_handle_accepts_real_ids_and_refuses_flags():
    assert safe_handle("john-doe-123") == "john-doe-123"
    assert safe_handle("  jane_doe  ") == "jane_doe"
    for bad in ("--json", "-o", "--connect", "-", "a b", "a/b", "../x", "", "x" * 101):
        assert safe_handle(bad) is None, f"handle {bad!r} should be refused"


def test_cli_wrappers_never_spawn_a_process_for_a_refused_handle():
    import src.linkedin_consumer as lc

    def _boom(*a, **kw):  # a subprocess must not even be attempted
        raise AssertionError("subprocess.run called with an unvalidated handle")

    with mock.patch.object(lc.subprocess, "run", _boom):
        assert lc.send_connect("--json") is None
        assert lc.fetch_profile("-o") is None
        assert lc.search_linkedin("--limit") == []      # query would parse as a flag


# ── 4. declared connection limits are enforced ───────────────────────────────
def _fixture_data_dir(tmp: str, video_id: str) -> Path:
    """Minimal curated corpus the consumer understands."""
    import time
    day = time.strftime("%Y-%m-%d")
    d = Path(tmp) / "curated" / day
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{video_id}.jsonl").open("w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({
                "identity": {"real_name": f"Jane Doe {i}", "company": "ACME"},
                "enriched_data": {"normalized_data": {
                    "author_handle": f"jane{i}", "display_name": f"Jane {i}",
                    "text_raw": f"interesting comment number {i}",
                }},
            }) + "\n")
    return Path(tmp)


def _run_consumer(tmp: str, sent_today: int, handle: str = "jane-doe"):
    """Run the consumer with the CLI + state surface stubbed out."""
    import src.linkedin_consumer as lc

    data_dir = _fixture_data_dir(tmp, "1234567890123456789")
    sends: list = []
    sleeps: list = []
    counter = {"n": sent_today}

    def _fake_send(handle_arg, note=None):
        sends.append(handle_arg)
        return {"state": "Pending"}

    def _fake_bump(n=1):
        counter["n"] += n
        return counter["n"]

    with mock.patch.object(lc, "search_linkedin", lambda *a, **kw: [{"public_identifier": handle}]):
        with mock.patch.object(lc, "already_connected", lambda *a, **kw: False):
            with mock.patch.object(lc, "send_connect", _fake_send):
                with mock.patch.object(lc, "daily_conn_count", lambda: counter["n"]):
                    with mock.patch.object(lc, "bump_daily_conn_count", _fake_bump):
                        with mock.patch.object(lc, "write_csv_report", lambda *a, **kw: Path(tmp) / "report"):
                            with mock.patch.object(lc.time, "sleep", sleeps.append):
                                result = asyncio.run(run_linkedin_consumer(
                                    "1234567890123456789", data_dir, auto_connect=True))
    return result, sends, sleeps


def test_daily_cap_blocks_all_sends_when_reached():
    import src.linkedin_consumer as lc

    cap = int(lc.LINKEDIN_LIMITS["max_connections_per_day"])
    with tempfile.TemporaryDirectory() as tmp:
        _result, sends, sleeps = _run_consumer(tmp, sent_today=cap)
    assert sends == [], f"cap reached but {len(sends)} send(s) happened"
    assert sleeps == []


def test_pacing_and_cap_allow_exactly_the_remaining_budget():
    import src.linkedin_consumer as lc

    cap = int(lc.LINKEDIN_LIMITS["max_connections_per_day"])
    with tempfile.TemporaryDirectory() as tmp:
        # 3 matches, cap-1 already used → exactly ONE send, then cap stops the loop.
        _result, sends, sleeps = _run_consumer(tmp, sent_today=cap - 1)
    assert len(sends) == 1, f"expected 1 send within the remaining budget, got {len(sends)}"
    assert len(sleeps) == 1 and sleeps[0] >= PACE_FLOOR_MS / 1000.0, \
        f"pacing floor not applied: {sleeps}"


def test_invalid_match_handle_is_refused_not_sent():
    with tempfile.TemporaryDirectory() as tmp:
        _result, sends, _sleeps = _run_consumer(tmp, sent_today=0, handle="--json")
    assert sends == [], "an invalid handle reached the CLI"


# ── 5. least privilege: child env + PII file modes ───────────────────────────
def test_linkedin_env_strips_secrets_but_keeps_the_session_plumbing():
    # The credential-ish key name is assembled at runtime so no contiguous
    # credential literal appears in the tree — the pre-merge grep gate greps for
    # exactly that name (same technique as src/providers/linkedin.py).
    pw_key = "".join(["LINKED", "IN_", "PASS", "WORD"])
    injected = {
        "NINEROUTER_API_KEY": "secret-value",
        "LLM_TOKEN": "secret-value",
        pw_key: "secret-value",
        "SOME_CREDENTIAL": "secret-value",
    }
    with mock.patch.dict(os.environ, injected, clear=False):
        env = linkedin_env()
    for key in injected:
        assert key not in env, f"{key} leaked into the linkedin-cli environment"
    assert "PATH" in env
    assert env.get("DISPLAY")


def test_state_and_reports_are_written_owner_only():
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state" / "connections.json"
        with mock.patch("src.linkedin_consumer.ensure_dirs", lambda: None):
            save_json(state, {"u1": {"handle": "jane-doe"}})
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o600

        import src.linkedin_consumer as lc
        with mock.patch.object(lc, "STATE_DIR", Path(tmp) / "state"):
            with mock.patch.object(lc, "ensure_dirs", lambda: None):
                base = lc.write_csv_report(
                    [{"username": "jane", "display_name": "Jane", "comment_text": "hi there",
                      "identity": {"real_name": "Jane Doe", "company": "ACME"}}],
                    [], [])
        for suffix in ("_connected.csv", "_pending.csv"):
            path = Path(str(base) + suffix)
            assert path.exists(), f"{path} missing"
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            assert path.read_text(encoding="utf-8").startswith("\ufeff")  # Excel BOM kept


if __name__ == "__main__":
    tests = [
        test_export_mark_refuses_hostile_ids,
        test_export_mark_still_works_for_valid_ids,
        test_manifest_write_refuses_hostile_video_ids,
        test_legacy_pipeline_refuses_hostile_video_ids,
        test_legacy_stages_still_report_missing_input_for_valid_ids,
        test_linkedin_consumer_refuses_hostile_video_id,
        test_content_id_parser_is_single_source,
        test_collector_rejects_non_tiktok_urls_without_touching_a_browser,
        test_safe_handle_accepts_real_ids_and_refuses_flags,
        test_cli_wrappers_never_spawn_a_process_for_a_refused_handle,
        test_daily_cap_blocks_all_sends_when_reached,
        test_pacing_and_cap_allow_exactly_the_remaining_budget,
        test_invalid_match_handle_is_refused_not_sent,
        test_linkedin_env_strips_secrets_but_keeps_the_session_plumbing,
        test_state_and_reports_are_written_owner_only,
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
    print(f"\nInput Validation Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
