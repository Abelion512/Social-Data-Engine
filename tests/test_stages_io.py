#!/usr/bin/env python3
"""
StageRunner output-layout guards (stdlib only, deterministic).

`_write_output` used to create ONE FILE PER RECORD (`000000.jsonl`,
`000001.jsonl`, …). On this machine that is 2 000 create/open syscalls for
2 000 records (≈140 ms, 2 000 inodes) versus ≈11 ms for a single JSONL file —
and 100k records meant 100k files in one directory.

These tests lock in the replacement contract:

  1. one `records.jsonl` per stage directory, written via `.tmp` + `os.replace`
  2. round-trip fidelity: same records, same order, same ids
  3. a shorter rewrite leaves NO stale records behind
  4. legacy per-record directories still load (upgrade path)
  5. a leftover `.tmp` is never read
  6. `run_stage` stays idempotent (second call skips, transform runs once)

Run:  python tests/test_stages_io.py
      python -m pytest tests/test_stages_io.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pipeline.stages import OUTPUT_FILENAME, StageRunner
from src.schema.canonical import Content, Observation, Provenance


def _obs(i: int) -> Observation:
    return Observation(
        observation_id=f"tiktok:c{i}",
        source="tiktok",
        content=Content(text_raw=f"hello {i}", text_normalized=f"hello {i}"),
        provenance=Provenance(collector_version="1.1.0", pipeline_version="1.1.0", source="tiktok"),
    )


def _records(n: int):
    return [_obs(i) for i in range(n)]


def _runner(tmp: str) -> StageRunner:
    return StageRunner(Path(tmp))


def test_single_file_and_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        out = r.dirs["curated"]
        records = _records(250)

        r._write_output(out, records)

        files = sorted(p.name for p in out.glob("*"))
        assert files == [OUTPUT_FILENAME], f"expected exactly one output file, got {files}"

        back = r._load_input(out)
        assert [o.observation_id for o in back] == [o.observation_id for o in records]
        assert [o.content.text_raw for o in back] == [o.content.text_raw for o in records]


def test_shorter_rewrite_leaves_no_stale_records():
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        out = r.dirs["curated"]

        r._write_output(out, _records(5))
        r._write_output(out, _records(2))

        back = r._load_input(out)
        assert [o.observation_id for o in back] == ["tiktok:c0", "tiktok:c1"], \
            "stale records survived a shorter rewrite"
        assert list(out.glob("*.jsonl")) == [out / OUTPUT_FILENAME]


def test_legacy_per_record_layout_still_loads():
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        out = r.dirs["curated"]
        out.mkdir(parents=True, exist_ok=True)
        for i in range(3):                       # pre-optimization layout
            (out / f"{i:06d}.jsonl").write_text(
                json.dumps(_obs(i).to_dict()) + "\n", encoding="utf-8")

        assert [o.observation_id for o in r._load_input(out)] == \
               ["tiktok:c0", "tiktok:c1", "tiktok:c2"]


def test_current_file_wins_over_legacy_leftovers():
    """A directory holding both layouts must not double-read records."""
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        out = r.dirs["curated"]
        r._write_output(out, _records(2))
        (out / "000000.jsonl").write_text('{"observation_id": "legacy"}\n', encoding="utf-8")

        assert [o.observation_id for o in r._load_input(out)] == ["tiktok:c0", "tiktok:c1"]


def test_leftover_temp_file_is_never_read():
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        out = r.dirs["curated"]
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{OUTPUT_FILENAME}.tmp").write_text('{"observation_id": "half"}\n', encoding="utf-8")

        assert r._load_input(out) == []


def test_run_stage_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        r = _runner(tmp)
        calls = []

        def transform(records):
            calls.append(len(records))
            return records

        first = r.run_stage("normalize", "raw", "curated", transform)
        second = r.run_stage("normalize", "raw", "curated", transform)

        assert calls == [0], f"transform should run once, ran {len(calls)} times"
        assert first == second


if __name__ == "__main__":
    tests = [
        test_single_file_and_round_trip,
        test_shorter_rewrite_leaves_no_stale_records,
        test_legacy_per_record_layout_still_loads,
        test_current_file_wins_over_legacy_leftovers,
        test_leftover_temp_file_is_never_read,
        test_run_stage_is_idempotent,
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
    print(f"\nStageRunner IO Suite: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
