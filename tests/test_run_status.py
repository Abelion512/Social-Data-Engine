#!/usr/bin/env python3
"""
Run status — the executable spec for `src/run_status.py` (the read-only
provenance view hosts get through the `sde_run_status` tool) and for the
`manifest.v1` schema stamp in `src/export/manifest.py`.

Pinned here, on a synthetic workspace (no real corpus, no browser):

  - real artifacts are read back with their real key names (checkpoint, loop
    state, improve manifest, per-video manifest, curated count)
  - a missing run is reported as `no_run_found` + an explicit note — never as a
    zero-filled record that would look like a completed run
  - a caller-supplied `video_id` that is not a slug is refused before any path
    is built (S-G2 / TM-13), and hostile filenames are skipped when scanning
  - every cap (runs, lines, bytes) reports itself instead of truncating quietly
  - the workspace root is injectable, so no test touches the repo's own `state/`
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.export.manifest import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    build_manifest,
    write_manifest,
)
from src.run_status import (  # noqa: E402
    MAX_RUNS,
    RUN_STATUS_SCHEMA_VERSION,
    collect_run_status,
)

VIDEO = "7673343206544706837"


class _Workspace:
    """A throwaway repo-shaped workspace with one recorded run."""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        for parts in (("state", "jobs"), ("state", "loops"), ("data", "manifests"),
                      ("data", "curated", "2026-09-21"), ("data", "curated", "2026-09-20")):
            (self.root.joinpath(*parts)).mkdir(parents=True, exist_ok=True)

    def write(self, *parts, payload=None, text=None) -> Path:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is not None:
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            path.write_text(text or "", encoding="utf-8")
        return path

    def checkpoint(self, video_id=VIDEO, **overrides) -> Path:
        data = {
            "job_id": "job_7673343206544706837_1",
            "video_id": video_id,
            "status": "done",
            "collection_status": "partial",
            "coverage": 0.22,
            "reported_comment_count": 200,
            "captured_comment_count": 44,
            "termination_reason": "auth_blocked",
            "started_at": "2026-09-21T01:00:00",
            "ended_at": "2026-09-21T01:04:00",
            "cursor": 250,
        }
        data.update(overrides)
        return self.write("state", "jobs", f"{video_id}.json", payload=data)

    def full_run(self) -> None:
        self.checkpoint()
        self.write("state", "loops", f"{VIDEO}.json", payload={
            "video_id": VIDEO, "job_id": "job_7673343206544706837_1",
            "next_iteration": 3, "outcome": "budget_exhausted",
            "updated_at": "2026-09-21T01:05:00"})
        self.write("data", "manifests", f"{VIDEO}.json",
                   payload={"schema_version": MANIFEST_SCHEMA_VERSION,
                            "video_id": VIDEO, "comment_count": 44})
        improve = "\n".join([
            json.dumps({"iteration": 1, "job_id": "j1", "actions": ["retry"],
                        "rationale": "coverage 0.22", "timestamp": "2026-09-21T01:02:00Z",
                        "policy_model_version": "1.0.0"}),
            json.dumps({"iteration": 2, "job_id": "j1", "actions": ["widen_scrolls"],
                        "rationale": "still partial", "timestamp": "2026-09-21T01:03:00Z"}),
            '{"iteration": 3, "torn',   # crash mid-append: must be reported, not hidden
        ]) + "\n"
        self.write("data", "manifests", f"{VIDEO}.improve.jsonl", text=improve)
        self.write("data", "curated", "2026-09-21", f"{VIDEO}.jsonl",
                   text="".join('{"comment_id": %d}\n' % i for i in range(3)))


class TestCollectRunStatus(unittest.TestCase):
    def test_reads_a_recorded_run_with_real_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            ws.full_run()
            payload = collect_run_status(root=ws.root, video_id=VIDEO)

            self.assertEqual(payload["schema_version"], RUN_STATUS_SCHEMA_VERSION)
            self.assertEqual(payload["count"], 1)
            run = payload["run"]
            self.assertEqual(run["status"], "recorded")
            self.assertEqual(run["job_id"], "job_7673343206544706837_1")
            self.assertEqual(run["checkpoint_status"], "done")
            self.assertEqual(run["collection_status"], "partial")
            self.assertEqual(run["coverage"], 0.22)
            self.assertEqual(run["termination_reason"], "auth_blocked")
            self.assertEqual(run["captured_comment_count"], 44)
            self.assertEqual(run["curated_records"], 3)
            self.assertEqual(run["manifest_schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertTrue(run["manifest_present"])
            self.assertEqual(run["improve_iteration_count"], 2)
            self.assertEqual([i["iteration"] for i in run["improve_iterations"]], [1, 2])
            self.assertEqual(run["loop"]["outcome"], "budget_exhausted")
            self.assertEqual(run["loop"]["next_iteration"], 3)
            self.assertTrue(any("rusak" in w for w in run["warnings"]),
                            f"torn append must be reported: {run['warnings']}")
            self.assertEqual(payload["sources"]["checkpoints"], "state/jobs/<video_id>.json")

    def test_missing_run_is_explicit_not_a_zero_filled_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = collect_run_status(root=Path(tmp), video_id="1234567890123456789")
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["runs"], [])
            self.assertEqual(payload["run"]["status"], "no_run_found")
            self.assertTrue(payload["notes"])
            self.assertIn("no_run_found", json.dumps(payload["run"]))

    def test_hostile_video_id_is_refused_before_any_path_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("../../etc/passwd", "a/b", "..", "", "UPPER", "../../../root",
                        "x" * 129, ".hidden"):
                with self.assertRaises(ValueError, msg=bad):
                    collect_run_status(root=Path(tmp), video_id=bad)

    def test_listing_skips_hostile_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            ws.checkpoint("1111111111111111111")
            ws.write("state", "jobs", "..json", payload={})
            ws.write("state", "jobs", "Bad Name.json", payload={})
            ws.write("state", "jobs", "UPPER.json", payload={})
            payload = collect_run_status(root=ws.root)
            self.assertEqual(payload["count"], 1)
            self.assertEqual([r["video_id"] for r in payload["runs"]],
                             ["1111111111111111111"])

    def test_empty_workspace_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = collect_run_status(root=Path(tmp))
            self.assertEqual(payload["count"], 0)
            self.assertTrue(any("tidak ada checkpoint" in note for note in payload["notes"]))

    def test_max_runs_is_clamped_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            for index in range(3):
                ws.checkpoint(f"12345678901234567{index:02d}")
            payload = collect_run_status(root=ws.root, max_runs=1)
            self.assertEqual(payload["count"], 1)
            self.assertTrue(any("hanya 1 run" in note for note in payload["notes"]))

            payload = collect_run_status(root=ws.root, max_runs=10_000)
            self.assertEqual(payload["count"], 3)
            self.assertEqual(payload["count"], min(3, MAX_RUNS))

    def test_oversized_manifest_is_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            ws.checkpoint()
            big = ws.write("data", "manifests", f"{VIDEO}.json",
                           text='{"video_id": "%s", "pad": "%s"}' % (VIDEO, "x" * 1_100_000))
            payload = collect_run_status(root=ws.root, video_id=VIDEO)
            self.assertGreater(big.stat().st_size, 1_000_000)
            run = payload["run"]
            self.assertFalse(run["manifest_present"])
            self.assertIsNone(run["manifest_schema_version"])
            self.assertTrue(any("dilewati" in w for w in run["warnings"]))

    def test_finished_checkpoint_without_curated_records_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            ws.checkpoint()  # done + no curated output anywhere
            run = collect_run_status(root=ws.root, video_id=VIDEO)["run"]
            self.assertTrue(any("curated records" in w for w in run["warnings"]),
                            run["warnings"])

    def test_corrupt_checkpoint_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _Workspace(tmp)
            ws.write("state", "jobs", f"{VIDEO}.json", text="{not json")
            payload = collect_run_status(root=ws.root, video_id=VIDEO)
            # An existing-but-unreadable artifact is NOT "no run": it is its own
            # status with the parse error attached.
            self.assertEqual(payload["run"]["status"], "unreadable")
            self.assertEqual(payload["count"], 1)
            self.assertTrue(any("tidak terbaca" in w for w in payload["run"]["warnings"]))
            self.assertTrue(any("tidak ada yang terbaca" in note for note in payload["notes"]))
            self.assertIn(f"{VIDEO}.json", payload["run"]["present_but_unreadable"])


class TestManifestSchemaStamp(unittest.TestCase):
    def test_build_manifest_stamps_the_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            curated = root / "data" / "curated" / "2026-09-21"
            curated.mkdir(parents=True)
            (curated / f"{VIDEO}.jsonl").write_text(
                json.dumps({"comment_id": "1", "identity": {"provider": "tiktok"}}) + "\n"
                + json.dumps({"comment_id": "2"}) + "\n", encoding="utf-8")
            (curated / "Bad Name.jsonl").write_text("{}\n", encoding="utf-8")

            manifest = build_manifest(root)
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["total_comments"], 2)
            self.assertEqual(manifest["total_with_identity"], 1)
            self.assertEqual([v["video_id"] for v in manifest["videos"]], [VIDEO])

    def test_empty_workspace_manifest_is_stamped_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_manifest(Path(tmp))
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["total_videos"], 0)

    def test_write_manifest_stamps_each_per_video_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "videos": [{"video_id": VIDEO, "comment_count": 1,
                            "manifest": {"stage": "curated"}}],
            }
            write_manifest(root, manifest)
            per_video = json.loads(
                (root / "data" / "manifests" / f"{VIDEO}.json").read_text(encoding="utf-8"))
            self.assertEqual(per_video["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(per_video["stage"], "curated")
            # a caller-provided value is never overwritten
            manifest["videos"][0]["manifest"]["schema_version"] = "custom.v9"
            write_manifest(root, manifest)
            again = json.loads(
                (root / "data" / "manifests" / f"{VIDEO}.json").read_text(encoding="utf-8"))
            self.assertEqual(again["schema_version"], "custom.v9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
