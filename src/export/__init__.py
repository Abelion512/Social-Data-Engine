"""
Multi-Consumer Export Layer — MARK format + manifest + CSV generation.

Modules:
  - mark: MARK Agent JSON export (video + corpus)  [optional, lazy]
  - manifest: Pipeline metadata generation
  - tocsv: flatten RawComment -> CSV table (nested reply + sticker + photo + voice)
"""
from src.export.tocsv import write_csv, csv_from_jsonl

try:
    from src.export.mark import export_video, export_all
except Exception:  # mark.py dapat rusak di deploy lintas-repo -> jangan bawa export lain
    export_video = export_all = None  # type: ignore

try:
    from src.export.manifest import build_manifest, write_manifest
except Exception:
    build_manifest = write_manifest = None  # type: ignore

__all__ = [
    "write_csv", "csv_from_jsonl",
    "export_video", "export_all",
    "build_manifest", "write_manifest",
]
