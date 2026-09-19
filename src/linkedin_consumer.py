#!/usr/bin/env python3
"""
LinkedIn Consumer — Match & Connect dari Curated Corpus

Membaca data dari pipeline (normalized/enriched/curated) → match LinkedIn → connect.
Terpisah dari collector agar TikTok Data Engine bisa dipakai consumer lain.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# Repo root on sys.path — needed when run as `python src/linkedin_consumer.py`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.runtime.context import require_slug_identifier, write_private_text  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────
STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"
CONNECTIONS_FILE = STATE_DIR / "connections.json"
DAILY_CONN_FILE = STATE_DIR / "daily_conn.json"

# ── Config ────────────────────────────────────────────────────────────────────
LINKEDIN_LIMITS = {
    # Enforced before every send (SAFETY/ACCEPTABLE-USE: bounded actions).
    "max_connections_per_day": 20,
    "min_delay_between_ms": 30,
}

# Floor for the inter-request delay. The configured 30 ms was never enforced and
# is not a safe pacing for an endpoint that flags rapid connection requests, so
# the effective delay is `max(configured, PACE_FLOOR_MS)`.
PACE_FLOOR_MS = 1000

# Handles reaching `linkedin-cli`: letters/digits, then letters/digits/underscore/
# hyphen, and NEVER starting with "-" (the leading-alnum rule is what keeps a
# scraped handle such as `--json` from being parsed as a CLI flag).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")

# TM-21 (least privilege): never hand secret-bearing environment variables to
# the linkedin-cli child process.
_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE)", re.I)


def safe_handle(handle: str) -> Optional[str]:
    """Return the handle when it is a valid LinkedIn public identifier, else None.

    Guards two things at once: argument injection into the `linkedin-cli` argv
    (a scraped handle like `--json`/`-o` would otherwise be parsed as a flag) and
    junk cells in the report/PII surface.
    """
    h = (handle or "").strip()
    return h if _HANDLE_RE.match(h) else None


# ── State helpers ─────────────────────────────────────────────────────────────
def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    """Write state JSON owner-only (0600): these files hold handles/profiles (PII)."""
    ensure_dirs()
    write_private_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def daily_conn_count() -> int:
    """Jumlah koneksi hari ini (berdasarkan file daily_conn.json)."""
    today = time.strftime("%Y-%m-%d")
    data = load_json(DAILY_CONN_FILE, {"date": today, "count": 0})
    if data.get("date") != today:
        return 0
    return data.get("count", 0)


def bump_daily_conn_count(n: int = 1) -> int:
    """Increment daily connection counter."""
    today = time.strftime("%Y-%m-%d")
    data = load_json(DAILY_CONN_FILE, {"date": today, "count": 0})
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    data["count"] += n
    save_json(DAILY_CONN_FILE, data)
    return data["count"]


def already_connected(handle: str) -> bool:
    """Cek apakah handle sudah connected/pending di state."""
    data = load_json(CONNECTIONS_FILE, {})
    for conn in data.values():
        if conn.get("handle") == handle:
            state = conn.get("state", "")
            if state in ("Connected", "Pending"):
                return True
    return False


def linkedin_env() -> Dict[str, str]:
    """Env untuk subprocess linkedin-cli — least privilege (TM-21).

    Strips PYTHONPATH *and* every secret-bearing variable (router/LLM keys,
    tokens, credentials, cookies) so a compromised or verbose child cannot read
    the parent's secrets out of its own environment.
    """
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV_RE.search(k)}
    env.pop("PYTHONPATH", None)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", os.path.expanduser("~/.Xauthority"))
    return env


# ── LinkedIn CLI wrappers ─────────────────────────────────────────────────────
def search_linkedin(name: str, company: Optional[str] = None) -> List[Dict]:
    """Search LinkedIn via linkedin-cli (darwincr/linkedin-cli)."""
    query = f"{name} {company or ''}".strip()
    if not query or query.startswith("-"):
        # Names come from scraped data → never let one become a CLI flag.
        print(f"[linkedin] refusing search query that could parse as a flag: {query!r}")
        return []
    try:
        result = subprocess.run(
            ["linkedin-cli", "search", query, "--limit", "3", "--json"],
            capture_output=True, text=True, timeout=60, env=linkedin_env(),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("profiles", [])
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[linkedin] search err: {e}")
    return []


def send_connect(handle: str, note: Optional[str] = None) -> Optional[Dict]:
    """Send LinkedIn connection request (darwincr fork: always no-note).

    `note` is kept for signature compatibility and deliberately unused: the
    pinned fork sends requests without a note. The handle is validated first so
    a scraped value can never be interpreted as a CLI flag.
    """
    clean = safe_handle(handle)
    if clean is None:
        print(f"[linkedin] refusing connect for invalid handle: {handle!r}")
        return None
    handle = clean
    cmd = ["linkedin-cli", "connect", handle, "--json"]
    last_err = None
    for attempt in range(1, 4):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=100, env=linkedin_env())
            if result.returncode == 0:
                return json.loads(result.stdout)
            err = result.stderr.strip().splitlines()
            last_err = err[-1] if err else f"rc={result.returncode}"
            print(f"[linkedin] connect {handle} (try {attempt}): {last_err}")
            if "profile_inaccessible" in last_err or "HTTP 403" in last_err:
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            last_err = str(e)
            print(f"[linkedin] connect {handle} (try {attempt}): {e}")
            if isinstance(e, (FileNotFoundError, json.JSONDecodeError)):
                return None
        if attempt < 3:
            time.sleep(attempt * 5)
    print(f"[linkedin] connect {handle}: giving up after 3 tries ({last_err})")
    return None


def fetch_profile(handle: str) -> Optional[Dict]:
    """Fetch profile by handle (ground truth from direct link)."""
    clean = safe_handle(handle)
    if clean is None:
        print(f"[linkedin] refusing profile fetch for invalid handle: {handle!r}")
        return None
    handle = clean
    try:
        result = subprocess.run(
            ["linkedin-cli", "profile", handle, "--json"],
            capture_output=True, text=True, timeout=60, env=linkedin_env(),
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get("public_identifier"):
                return data
        err = result.stderr.strip().splitlines()
        print(f"[linkedin] profile {handle}: {err[-1] if err else f'rc={result.returncode}'}")
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[linkedin] profile err: {e}")
    return None


def generate_note(profile: Dict, comment: Dict) -> str:
    """Generate connection note dari profile + comment."""
    name = profile.get("full_name", "there")
    return f"Hi {name}! Noticed your comment on TikTok — thought we should connect."


# ── Post-processing helpers ───────────────────────────────────────────────────
def cleanse_comments(comments: List[Dict]) -> List[Dict]:
    """Drop junk rows: empty text, pure-emoji, spammy URLs, unbalanced quotes."""
    cleaned = []
    for c in comments:
        text = (c.get("comment_text") or "").strip()
        imgs = c.get("images") or []
        if len(text) < 3 and not imgs:
            continue
        if text.lower().startswith(("linking", "add comment")):
            continue
        if text.count("http") > 1:
            continue
        if not c.get("username") and c.get("display_name"):
            c["username"] = c["display_name"]
        cleaned.append(c)
    return cleaned


def verify_names(comments: List[Dict]) -> Dict:
    """Check extracted names are plausible (>=2 chars, no digits-only, sane ratio)."""
    ok = 0
    issues = []
    for c in comments:
        identity = c.get("identity") or {}
        name = identity.get("real_name", "")
        if not name:
            issues.append((c.get("username", "?"), "no real_name"))
            continue
        if len(name) < 2 or not any(ch.isalpha() for ch in name):
            issues.append((c.get("username", "?"), f"bad name: {name!r}"))
            continue
        ok += 1
    return {"verified": ok, "total": len(comments), "issues": issues[:10]}


def write_csv_report(comments: List[Dict], matches: List[Dict], connections: List[Dict]) -> Path:
    """CSV dengan satu row per comment: scraped/enriched/matched/connected status.

    Reports carry personal data (real names, employers, profile URLs, comment
    text), so they are written owner-only (0600) via `write_private_text` instead
    of a world-readable default `open()`. The UTF-8 BOM is preserved for Excel.
    """
    import csv as _csv
    import io as _io

    conn_map = {conn.get("profile"): conn.get("result") for conn in connections}
    match_by_name = {}
    match_by_user = {}
    for m in matches:
        identity = m.get("identity") or {}
        match_by_name[identity.get("real_name", "")] = m.get("linkedin", {})
        match_by_user[m.get("comment", {}).get("username", "")] = m.get("linkedin", {})

    ts = time.strftime("%Y%m%d_%H%M%S")
    rows = []
    for i, c in enumerate(comments, 1):
        identity = c.get("identity") or {}
        name = identity.get("real_name", "")
        profile = match_by_user.get(c.get("username", "")) or match_by_name.get(name, {})
        handle = profile.get("public_identifier") or profile.get("handle", "")
        if handle in conn_map:
            r = conn_map[handle]
            state = (r or {}).get("state", "Pending")
            connected = "YES" if state == "Connected" else ("FAILED" if r is None else "PENDING")
        else:
            connected = "NO"
        profile_url = f"https://www.linkedin.com/in/{handle}" if handle else ""
        rows.append([
            i, c.get("username", ""), c.get("display_name", ""), name,
            identity.get("company", ""), c.get("comment_text", ""),
            "YES", "YES" if name else "NO",
            "YES" if profile else "NO", profile_url, connected,
            "no-note" if handle and handle in conn_map else "",
        ])

    header = [
        "no", "tiktok_username", "display_name", "real_name", "company",
        "comment_text", "scraped", "name_verified", "linkedin_match",
        "linkedin_url", "connected", "note",
    ]
    base = STATE_DIR / f"report_{ts}"
    for suffix, pred in (("_connected.csv", lambda r: r[10] == "YES"),
                         ("_pending.csv", lambda r: r[10] != "YES")):
        path = Path(str(base) + suffix)
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(header)
        for r in rows:
            if pred(r):
                w.writerow(r)
        write_private_text(path, "\ufeff" + buf.getvalue())   # BOM + mode 0600
        print(f"[csv] {path.name}: {sum(1 for r in rows if pred(r))} rows")
    return base


# ── Pipeline Consumer ─────────────────────────────────────────────────────────
async def run_linkedin_consumer(
    video_id: str,
    data_dir: Path,
    auto_connect: bool = False,
    max_comments: int = 100,
    no_note: bool = False,
) -> Dict:
    """
    Consumer LinkedIn dari curated corpus.
    Baca: data/curated/<date>/<video_id>.jsonl
    Output: CSV report di STATE_DIR
    """
    # S-G2/TM-13: the id arrives from the CLI, so validate it before it becomes
    # a path component (`--video ../../etc/passwd` must not read outside data/).
    video_id = require_slug_identifier(video_id, "video_id")
    today = time.strftime("%Y-%m-%d")
    curated_file = data_dir / "curated" / today / f"{video_id}.jsonl"
    if not curated_file.exists():
        # Fallback ke normalized jika curated belum ada
        curated_file = data_dir / "normalized" / today / f"{video_id}.deduped.jsonl"
    if not curated_file.exists():
        return {"error": "no_curated_data", "video_id": video_id}

    comments = []
    with curated_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # Transform ke format lama untuk kompatibilitas
            enriched_data = rec.get("enriched_data", rec)
            normalized = enriched_data.get("normalized_data", enriched_data)
            c = {
                "username": normalized.get("author_handle", ""),
                "display_name": normalized.get("display_name", ""),
                "comment_text": normalized.get("text_raw", ""),
                "images": normalized.get("images", []),
                "identity": rec.get("identity"),
            }
            comments.append(c)

    comments = cleanse_comments(comments)
    comments = comments[:max_comments]
    print(f"[linkedin_consumer] Loaded {len(comments)} comments from curated")

    # Extract identities via LLM (placeholder — gunakan pipeline enrichment)
    # Untuk sekarang, assume identity sudah ada di curated/enriched
    matches = []
    for c in comments:
        identity = c.get("identity")
        if identity and identity.get("real_name"):
            linkedin_profiles = search_linkedin(identity["real_name"], identity.get("company"))
            if linkedin_profiles:
                matches.append({
                    "comment": c,
                    "identity": identity,
                    "linkedin": linkedin_profiles[0],
                })

    connections = []
    sends_refused = 0
    if auto_connect and matches:
        cap = int(LINKEDIN_LIMITS["max_connections_per_day"])
        pace_s = max(int(LINKEDIN_LIMITS["min_delay_between_ms"]), PACE_FLOOR_MS) / 1000.0
        sent_today = daily_conn_count()
        if sent_today >= cap:
            print(f"[linkedin] daily connection cap already reached ({sent_today}/{cap}) — no sends")
        for m in matches:
            if sent_today >= cap:
                print(f"[linkedin] daily cap reached ({sent_today}/{cap}) — stopping sends")
                break
            profile = m.get("linkedin", {})
            raw_handle = profile.get("public_identifier") or profile.get("handle", "")
            handle = safe_handle(raw_handle)
            if handle is None:
                sends_refused += 1
                print(f"[linkedin] skip invalid handle: {raw_handle!r}")
                continue
            if already_connected(handle):
                continue
            note = None if no_note else generate_note(profile, m.get("comment", {}))
            result = send_connect(handle, note)
            if result:
                connections.append({"profile": handle, "result": result})
                sent_today = bump_daily_conn_count()
                time.sleep(pace_s)      # pacing floor — never hammer the endpoint
    if sends_refused:
        print(f"[linkedin] {sends_refused} handle(s) refused by validation")

    report_path = write_csv_report(comments, matches, connections)
    return {
        "video_id": video_id,
        "comments_processed": len(comments),
        "matches": len(matches),
        "connections": len(connections),
        "report": str(report_path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    import argparse
    import asyncio
    parser = argparse.ArgumentParser(description="LinkedIn Consumer dari Curated Corpus")
    parser.add_argument("video_id", help="Video ID to process")
    parser.add_argument("--data-dir", default=str(Path(__file__).parent / "data"), help="Data directory root")
    parser.add_argument("--auto-connect", action="store_true", help="Auto send connection requests")
    parser.add_argument("--max", type=int, default=100, help="Max comments to process")
    parser.add_argument("--no-note", action="store_true", help="Send connection without note")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    asyncio.run(run_linkedin_consumer(
        video_id=args.video_id,
        data_dir=data_dir,
        auto_connect=args.auto_connect,
        max_comments=args.max,
        no_note=args.no_note,
    ))


if __name__ == "__main__":
    main()