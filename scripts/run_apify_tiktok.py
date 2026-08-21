#!/usr/bin/env python3
"""
Apify TikTok Comments Scraper — wrapper (agent + CLI friendly).

Menggunakan `apify-client` untuk run actor `clockworks/tiktok-comments-scraper`.
IMPORTANT: kami **tidak meng-invent input field names**. Field diambil **langsung**
dari file JSON yang kamu sediakan (`--input`) — jadi *exact schema* kamu berlaku.

  Option b) library : pip install apify-client
  Option c) one-off : curl ke run-sync-get-dataset-items (lihat docs/REFERENCES.md)
  Option d) CLI     : npm i -g apify-cli && apify call clockworks/tiktok-comments-scraper --input <json>

Prasyarat:
  - APIFY_TOKEN dari https://console.apify.com/settings/integrations
    (atau `apify login` dulu; atau pakai MCP OAuth — lihat opsi a) di docs/REFERENCES.md)
  - File input JSON: contoh (sesuaikan field apa adanya di Console):
        {"postUrl": "https://www.tiktok.com/@user/video/123", "maxComments": 500}
"""
from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path

try:
    from apify_client import ApifyClient
except ImportError:
    ApifyClient = None  # type: ignore

DEFAULT_ACTOR = "clockworks/tiktok-comments-scraper"


def _token() -> str | None:
    return os.environ.get("APIFY_TOKEN")


def _client():
    if ApifyClient is None:
        raise SystemExit(
            "apify-client belum terpasang. Install:\n"
            "    .venv/bin/python -m pip install apify-client\n"
            "(atau pakai option c) curl — lihat docs/REFERENCES.md)"
        )
    token = _token()
    if not token:
        raise SystemExit(
            "APIFY_TOKEN tidak ada. Set di env:\n"
            "    export APIFY_TOKEN='<token-dari-console.apify.com/settings/integrations>'\n"
            "atau jalankan `apify login` dulu (option d/e)."
        )
    return ApifyClient(api_token=token)


def _load_input(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"file input tidak ada: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def run_actor(video_url: str | None = None,
              input_file: str | None = None,
              actor_id: str = DEFAULT_ACTOR,
              output_file: str | None = None,
              max_items: int | None = None,
              wait_secs: int = 60,
              dry_run: bool = False) -> dict:
    """Run TikTok Comments Scraper actor.

    Parameters (semua *exact field* milik kamu, tidak di-invent):
      video_url  : jika diberi & belum pakai file, dipasangkan ke field khas
                   ('postUrl'/'videoUrl'/'url'/'tiktokUrl') — **ganti sesuai schema
                   Console kamu** (lihat docs/REFERENCES.md).
      input_file : file JSON dengan field schema-mu (exact, tidak kami ubah).
      max_items  : batas dataset items yang diambil.
      dry_run    : print apa yang akan dikirim, jalan tanpa eksekusi.
    Returns: dict run + dataset items.
    """
    inp = _load_input(input_file)

    # convenience: URL saja (jika kamu belum siapkan file JSON)
    if video_url and not inp:
        for field in ("postUrl", "videoUrl", "url", "tiktokUrl"):
            inp[field] = video_url
            break
        else:
            inp["postUrl"] = video_url

    if dry_run:
        print("[dry-run] input yang akan dikirim:")
        print(json.dumps(inp, indent=2, ensure_ascii=False))
        return {"input": inp, "ran": False}

    client = _client()
    print(f"[apify] run actor: {actor_id}")
    print(f"[apify] input: {json.dumps(inp, ensure_ascii=False)[:300]}")

    run = client.actor(actor_id).call(run_input=inp, max_items=max_items)
    dataset_id = run.get("defaultDatasetId")
    items = []
    if dataset_id:
        items = list(client.dataset(dataset_id).iterate_items())
        print(f"[apify] diterima {len(items)} item dari dataset.")
        for it in items[:3]:
            print("  ", json.dumps(it, ensure_ascii=False)[:200])

    result = {"run": run, "items_count": len(items), "items": items}
    if output_file:
        Path(output_file).write_text(
            json.dumps(result, ensure_ascii=False, indent=2))
        print(f"[apify] hasil disimpan {output_file}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Run Apify TikTok Comments Scraper (exact input fields via --input).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("url", nargs="?", help="URL(video) TikTok (convenience field)")
    g.add_argument("-i", "--input", help="File JSON dengan field schema-mu (exact)")
    ap.add_argument("-o", "--output", help="Simpan hasil JSON ke file")
    ap.add_argument("--actor", default=DEFAULT_ACTOR,
                    help=f"Actor id (default: {DEFAULT_ACTOR})")
    ap.add_argument("--max-items", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print apa yang akan dikirim, jalan tanpa eksekusi")
    args = ap.parse_args(argv)

    try:
        res = run_actor(
            video_url=args.url,
            input_file=args.input,
            actor_id=args.actor,
            output_file=args.output,
            max_items=args.max_items,
            dry_run=args.dry_run,
        )
    except SystemExit as e:
        print("ERROR:", e)
        return 1

    ok = res.get("items_count", 0) > 0 or args.dry_run
    return 0 if ok else 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
