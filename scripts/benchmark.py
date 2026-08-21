#!/usr/bin/env python3
"""
Benchmark runner for TikTok collection + self-improvement loop.

Didesain *functional* (bukan estetis): mengukur metrik akurat per run,
menyimpannya di `data/benchmarks/<run_id>.jsonl`, dan mengembalikan ringkasan
untuk keputusan "stable / needs retry / provider swap".

Metric alignment: sama persis dengan `src/pipeline/improve.py::PipelineMetrics`
(coverage, captured, dup_rate, avg_quality, partial, stall_reason) + tambahan
runtime (elapsed, browser_source, n_iterations).

Usage (Linux / bash / zsh):
    # Dry-run (bisa dijalankan di mana saja — tidak butuh browser):
    .venv/bin/python scripts/benchmark.py --dry-run

    # Live (butuh browser CDP terbuka / login cookie):
    .venv/bin/python scripts/benchmark.py --urls \
        "https://www.tiktok.com/@coretanmalam2000/photo/7673343206544706837" \
        "https://www.tiktok.com/@drew_chelle/video/7620779574355758356" \
        --max-comments 300 --max-scrolls 60

    # Dari file daftar URL:
    .venv/bin/python scripts/benchmark.py --urls-file urls.txt

    # Agregasi ulang dari manifest improve (run sebelumnya, tanpa scrape lagi):
    .venv/bin/python scripts/benchmark.py --from-manifests data/manifests
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from src.tiktok_schema import COLLECTOR_VERSION
except Exception:
    COLLECTOR_VERSION = "unknown"

# Installable/portable data root — set $SDE_DATA_DIR untukarahkan ke mana saja
# (sama portability konsep .venv). Default → repo-root/data.
_DATA_ROOT = Path(os.environ.get("SDE_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
BENCH_DIR = _DATA_ROOT / "benchmarks"
MANIFEST_DIR = _DATA_ROOT / "manifests"


def _git_describe() -> str:
    """git describe --tags (untuk compare across runs); fallback bila non-git/tanpa tag."""
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stderr=subprocess.DEVNULL, text=True).strip() or "noversion"
    except Exception:
        return "noversion"


# Satu nilai per run → tiap record di dalam run pakai ini (compare-by-version).
RUN_VERSION = f"{COLLECTOR_VERSION}-{_git_describe()}"


@dataclass
class RunMetrics:
    video_id: str
    url: str
    version: str = RUN_VERSION          # untuk banding versi (compare-by-version)
    elapsed_sec: float = 0.0
    browser_source: str = "unknown"      # cdp | camoufox | none
    n_iterations: int = 0
    reported: int = 0
    captured: int = 0
    coverage: float = 0.0
    dup_rate: float = 0.0
    avg_quality: float = 0.0
    partial: bool = True
    stall_reason: str = ""
    ok: bool = False                     # True bila pass (coverage>=0.95 & quality>=0.35)



def _new_run_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _record(run_id: str, m: RunMetrics) -> None:
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    p = BENCH_DIR / f"{run_id}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")


def _aggregate(run_id: str) -> None:
    rows = [json.loads(l) for l in (BENCH_DIR / f"{run_id}.jsonl").read_text().splitlines() if l.strip()]
    n = len(rows)
    avg_cov = sum(r["coverage"] for r in rows) / n if n else 0
    avg_q = sum(r["avg_quality"] for r in rows) / n if n else 0
    avg_t = sum(r["elapsed_sec"] for r in rows) / n if n else 0
    src = {}
    for r in rows:
        src[r["browser_source"]] = src.get(r["browser_source"], 0) + 1
    ok = sum(1 for r in rows if r["ok"])
    print("\n=== 📊 BENCHMARK SUMMARY ===")
    print(f"  run_id        : {run_id}")
    print(f"  version       : {rows[0].get('version', RUN_VERSION) if rows else RUN_VERSION}")
    print(f"  videos        : {n}")
    print(f"  pass (stable) : {ok}/{n}")
    print(f"  avg coverage  : {avg_cov:.1%}")
    print(f"  avg quality   : {avg_q:.3f}")
    print(f"  avg elapsed   : {avg_t:.1f}s")
    print(f"  src mix       : {src}")


def _compare_versions() -> None:
    """Banding semua run di data/benchmarks/ per version (compare-by-version)."""
    import glob, collections
    files = sorted(glob.glob(str(BENCH_DIR / "*.jsonl")))
    if not files:
        print("[compare] tidak ada run benchmark — jalankan dulu `benchmark.py --dry-run` atau live run.")
        return
    # group rows by version
    by_ver: dict[str, list] = collections.defaultdict(list)
    for f in files:
        rid = Path(f).stem
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_ver[row.get("version", "?")].append((rid, row))

    print("=== 📊 COMPARE BY VERSION ===")
    print(f"{'version':<28}{'runs':>6}{'videos':>8}{'pass':>6}{'avg_cov%':>10}{'avg_q':>8}{'avg_sec':>10}{'src':>22}")
    for ver, rows in sorted(by_ver.items(), key=lambda kv: kv[0], reverse=True):
        n = len(rows)
        nv = len({r[0] for r in rows})
        avg_cov = sum(r[1]["coverage"] for r in rows) / n
        avg_q = sum(r[1]["avg_quality"] for r in rows) / n
        avg_t = sum(r[1]["elapsed_sec"] for r in rows) / n
        ok = sum(1 for r in rows if r[1]["ok"])
        src = collections.Counter(r[1]["browser_source"] for r in rows)
        src_str = " ".join(f"{k}:{v}" for k, v in src.items())
        print(f"{ver:<28}{nv:>6}{n:>8}{ok:>6}{avg_cov*100:>9.1f}%{avg_q:>8.3f}{avg_t:>10.1f}{src_str:>22}")


# ── Live run (butuh browser) ──────────────────────────────────────────────────
async def _live_one(url: str, max_comments: int, max_scrolls: int) -> RunMetrics:
    from src.collector import collect_video  # import lazy → tetap bisa import di luar
async def _live_one(url: str, max_comments: int, max_scrolls: int,
                    force_camoufox: bool = False) -> RunMetrics:
    from src.collector import collect_video  # import lazy → tetap bisa import di luar
    from src.browser_selector import BrowserSession  # noqa: F401 (lazy ref; session managed by collect_video)

    video_id = url.rstrip("/").rsplit("/", 1)[-1]
    t0 = time.time()
    source = "none"
    ok = False
    reported = captured = 0
    coverage = 0.0
    result: dict = {}
    stall = ""
    try:
        # `collect_video` ADALAH orang yang connect browser (CDP → camoufox
        # fallback) — jangan double-attach di sini (dulu bikin
        # `TypeError: collect_video(..., page=...)`). collect_video() return:
        # {video_id, comments(captured), mode(cdp/camoufox),
        #  reported_comment_count, coverage, collection_status, ...}
        result = await collect_video(
            video_url=url,
            max_scrolls=max_scrolls,
            max_comments=max_comments,
            force_camoufox=force_camoufox,   # True → camoufox; False → CDP (auto cookie)
        )
        if isinstance(result, dict) and not result.get("error"):
            source = result.get("mode", "unknown")
            captured = int(result.get("comments", 0))
            reported = int(result.get("reported_comment_count", 0) or 0)
            cov = result.get("coverage")
            coverage = float(cov if cov is not None else 0.0)
            ok = captured > 0
        else:
            stall = result.get("error", "unknown_error") if isinstance(result, dict) else "no_result"
            source = result.get("mode", "none") if isinstance(result, dict) else "none"
    except Exception as e:
        stall = f"{type(e).__name__}: {str(e)[:80]}"
    elapsed = time.time() - t0
    return RunMetrics(
        video_id=video_id, url=url, elapsed_sec=elapsed,
        browser_source=source, n_iterations=result.get("n_iterations", 1) if isinstance(result, dict) else 1,
        reported=reported, captured=captured,
        coverage=coverage,
        avg_quality=0.0, partial=not ok, stall_reason=stall, ok=ok,
    )


def _run_live(urls: List[str], max_comments: int, max_scrolls: int,
              force_camoufox: bool = False) -> None:
    """Single event loop untuk semua video (avoid 'Event loop is closed' noise
    yang timbul bila pakai asyncio.run() per-URL berulang — terutama saat
    camoufox subprocess teardown)."""
    import asyncio
    rid = _new_run_id()
    print(f"[benchmark] run_id={rid}  version={RUN_VERSION}")

    async def _loop():
        for u in urls:
            m = await _live_one(u, max_comments, max_scrolls, force_camoufox=force_camoufox)
            _record(rid, m)
            print(f"  - {m.video_id[:12]}… source={m.browser_source} "
                  f"captured={m.captured} elapsed={m.elapsed_sec:.1f}s ok={m.ok} stall={m.stall_reason!r}")

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        print("\n[benchmark] interrupted by user")
    finally:
        # pastikan semua loop/subprocess camoufox tertutup bersih
        try:
            import gc
            gc.collect()
        except Exception:
            pass
    _aggregate(rid)


def _from_manifests(manifest_dir: Path) -> None:
    """Agretasi benchmark dari manifest improve yang sudah ada (tanpa scrape)."""
    import glob
    files = sorted(glob.glob(str(manifest_dir / "*.improve.jsonl")))
    if not files:
        print(f"[benchmark] tidak ada manifest di {manifest_dir}")
        return
    rid = _new_run_id()
    seen_vid = set()
    for f in files:
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            vid = rec.get("metrics", {}).get("video_id", Path(f).stem.split(".")[0])
            if vid in seen_vid:
                continue
            seen_vid.add(vid)
            mt = rec.get("metrics", {}) or {}
            _record(rid, RunMetrics(
                video_id=vid, url="",
                n_iterations=rec.get("iteration", 0),
                reported=mt.get("reported", 0),
                captured=mt.get("captured", 0),
                coverage=mt.get("coverage", 0.0),
                dup_rate=mt.get("dup_rate", 0.0),
                avg_quality=mt.get("avg_quality", 0.0),
                partial=mt.get("partial", True),
                stall_reason=mt.get("stall_reason", ""),
                ok=_is_stable(mt),
            ))
    _aggregate(rid)


def _is_stable(mt: dict) -> bool:
    return (not mt.get("partial", True)
            and (mt.get("coverage") or 0) >= 0.95
            and (mt.get("avg_quality") or 0) >= 0.35)


def _dry_run(urls: List[str]) -> None:
    """Sample run — tidak butuh browser, hanya print format output."""
    rid = _new_run_id()
    print(f"[benchmark][dry-run] run_id={rid}")
    print(f"[benchmark] would scrape {len(urls)} video(s):")
    for u in urls:
        print(f"  - {u}")
    sample = RunMetrics(
        video_id="7673343206544706837", url=urls[0] if urls else "tiktok.com/@user/video/X",
        elapsed_sec=42.5, browser_source="cdp", n_iterations=2,
        reported=987, captured=942, coverage=0.954, dup_rate=0.04,
        avg_quality=0.412, partial=False, stall_reason="", ok=True)
    _record(rid, sample)
    print(f"[benchmark] sample recorded → {BENCH_DIR / f'{rid}.jsonl'}")
    _aggregate(rid)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--urls", nargs="*", help="daftar URL video (live run)")
    ap.add_argument("--urls-file", help="file teks URL per baris")
    ap.add_argument("--max-comments", type=int, default=300)
    ap.add_argument("--max-scrolls", type=int, default=60)
    ap.add_argument("--from-manifests", type=str,
                    help="agregasi ulang dari direktory manifest improve (tanpa scrape)")
    ap.add_argument("--dry-run", action="store_true", help="mode sample (tidak butuh browser)")
    ap.add_argument("--camoufox", action="store_true",
                    help="force launch Camoufox (melewat CDP detection) — pakai bila CDP blocked")
    ap.add_argument("--compare-versions", action="store_true",
                    help="bandingkan semua run di data/benchmarks/ per version")
    args = ap.parse_args(argv)

    if args.compare_versions:
        _compare_versions()
        return 0

    if args.urls_file:
        args.urls = [l.strip() for l in Path(args.urls_file).read_text().splitlines() if l.strip()]

    if args.dry_run:
        _dry_run(args.urls or ["https://www.tiktok.com/@coretanmalam2000/photo/7673343206544706837"])
        return 0
    if args.from_manifests:
        _from_manifests(Path(args.from_manifests))
        return 0
    if not args.urls:
        ap.print_help(); return 1
    _run_live(args.urls, args.max_comments, args.max_scrolls, force_camoufox=args.camoufox)
    return 0


if __name__ == "__main__":
    sys.exit(main())
