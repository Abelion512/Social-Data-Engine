#!/usr/bin/env python3
"""
Transparent end-to-end traceability — "sampai tuntas/transparan".

Given a {video_id, comment_id}, follow that one comment through EVERY pipeline
stage (raw → normalized → deduped → enriched → curated → manifest) and print:
  - which file it lives in at each stage
  - capture_method, parent_comment_id, quality, reject reason
  - cross-stage lineage assert (same comment_id survives / rejected deterministically)

Exit non-zero if the comment vanishes mid-pipeline WITHOUT a recorded reject
reason → surfaces silent data loss (e.g. the dedup-parent bug we just fixed).

Usage:
  python scripts/trace_comment.py --video 7669640839861112071 --comment 7670523593819341576
  python scripts/trace_comment.py --video 7669640839861112071 --trace-all     # trace every curated
"""
from __future__ import annotations

import argparse
import json
import glob
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


STAGES = [
    ("raw", "data/raw"),
    ("normalized", "data/normalized"),
    ("deduped", "data/normalized"),      # .deduped.jsonl
    ("enriched", "data/enriched"),
    ("curated", "data/curated"),
]
DATE = "2026-08-20"


def _files(root: Path, stage: str, video_id: str):
    d = root / STAGES[0][1] if False else (root / {"raw": "data/raw",
                    "normalized": "data/normalized",
                    "deduped": "data/normalized",
                    "enriched": "data/enriched",
                    "curated": "data/curated"}[stage]) / DATE
    if stage == "deduped":
        pat = f"{video_id}.deduped.jsonl"
    else:
        pat = f"{video_id}.jsonl"
    return list(d.glob(pat))


def load_rows(path: Path):
    return [json.loads(l) for l in path.open() if l.strip()]


def _meta(r: dict):
    c = (r.get("content") or {}) if isinstance(r.get("content"), dict) else {}
    md = c.get("metadata", r) if isinstance(c.get("metadata"), dict) else {}
    meta = {**(md or {})}
    # flat fallback
    meta.setdefault("parent_comment_id", r.get("parent_comment_id", ""))
    meta.setdefault("capture_method", r.get("capture_method", ""))
    meta.setdefault("comment_id", r.get("comment_id", ""))
    md_q = r.get("quality")
    q = md_q.get("quality") if isinstance(md_q, dict) else r.get("quality")
    meta["quality"] = round(float(q), 4) if q else None
    meta["_dedup_reject"] = r.get("_dedup_reject")
    return meta


def trace_comment(video_id: str, comment_id: str):
    print(f"\n🔍 TRACE comment={comment_id}  video={video_id}")
    found = {}
    reject_chain = []
    for stage, _ in STAGES:
        files = _files(_ROOT, stage, video_id)
        loc = files[0] if files else None
        rec = None
        if loc:
            for row in load_rows(loc):
                r = _meta(row)
                if r.get("comment_id") == comment_id or (stage == "curated" and row.get("comment_id") == comment_id):
                    rec = r
                    break
        if rec:
            found[stage] = {"file": str(loc) if loc else None, **rec}
            print(f"  [{stage:11s}] ✅ file={Path(loc).name if loc else '-'} "
                  f"capture={rec.get('capture_method','?')} parent={rec.get('parent_comment_id','?')[:12] or '-'} "
                  f"quality={rec.get('quality','?')} reject={rec.get('_dedup_reject','-')}")
        else:
            # check rejected dump
            rej_pat = f"data/rejected/{DATE}/{video_id}.dups.jsonl"
            rx = _ROOT / rej_pat
            rej = []
            if rx.exists():
                for row in load_rows(rx):
                    if row.get("comment_id") == comment_id:
                        rej.append(row.get("_dedup_reject"))
            tag = "REJECTED" if rej else "LOST"
            print(f"  [{stage:11s}] ❌ {tag} " + (f"(reason={rej[-1]})" if rej else "(silent data LOSS!)"))
            if rej:
                reject_chain.append(rej[-1])
            return found, reject_chain
    return found, reject_chain


def assert_lineage(found):
    """Cross-stage comment_id consistency check."""
    print("\n  ─ lineage assertion ─")
    ok = True
    if "raw" in found and "curated" in found:
        cid_r = found["raw"].get("comment_id")
        cid_c = found["curated"].get("comment_id")
        same = cid_r == cid_c
        print(f"  raw.id == curated.id : {same} ({cid_r} vs {cid_c})")
        ok &= same
    if "normalized" in found and "deduped" in found:
        ok &= found["normalized"].get("parent_comment_id") == found["deduped"].get("parent_comment_id")
        print(f"  parent survives dedup: {ok}")
    if "curated" in found:
        q = found["curated"].get("quality")
        gated = q is not None and q >= 0.35
        print(f"  quality gate >=0.35   : {gated} (q={q})")
        ok &= gated
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--comment", help="comment_id to trace")
    ap.add_argument("--trace-all", action="store_true", help="trace all curated comments")
    args = ap.parse_args()

    if args.trace_all:
        cf = _ROOT / f"data/curated/{DATE}/{args.video}.jsonl"
        if not cf.exists():
            print("no curated file"); sys.exit(1)
        cids = [r.get("comment_id") for r in load_rows(cf) if r.get("comment_id")]
        all_ok = True
        for cid in cids:
            f, rej = trace_comment(args.video, cid)
            ok = assert_lineage(f)
            all_ok &= ok
        print(f"\n{'✅ ALL' if all_ok else '❌ SOME'} {len(cids)} curated comments traced deterministically")
        sys.exit(0 if all_ok else 1)
    else:
        f, rej = trace_comment(args.video, args.comment)
        ok = assert_lineage(f)
        alive = "curated" in f
        tag = "✅ TRACE OK" if (ok and alive) else "❌ TRACE FAIL"
        print(f"\n{tag}: comment survives pipeline={alive}, lineage_ok={ok}, reject_chain={rej}")
        sys.exit(0 if (ok and alive) else 1)


if __name__ == "__main__":
    main()
