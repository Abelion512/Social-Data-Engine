#!/usr/bin/env python3
"""
TikTok → LinkedIn Connect Pipeline (rich consumer)

Akuisisi & transformasi kini didelegasikan:
  collector.collect_video()   → data/raw/       (L0)
  pipeline.run_video()        → data/curated/   (L1-L3)

File ini hanya konsumen LinkedIn: identity LLM → match (link/photo/name)
→ connect (with circuit breaker) → CSV report.

Usage:
  python tiktok_linkedin.py --login                    # Login TikTok manual
  python tiktok_linkedin.py <tiktok_url>               # Collect + match
  python tiktok_linkedin.py <tiktok_url> --connect     # Auto-connect
  python tiktok_linkedin.py <tiktok_url> --max 50      # Limit comments
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import collector
import pipeline
from linkedin_consumer import (
    LINKEDIN_LIMITS,
    CONNECTIONS_FILE,
    already_connected,
    bump_daily_conn_count,
    cleanse_comments,
    daily_conn_count,
    fetch_profile,
    generate_note,
    load_json,
    save_json,
    search_linkedin,
    send_connect,
    verify_names,
    write_csv_report,
)

# ── Config (LLM enrichment — tetap di consumer, bukan pipeline) ───────────────
DEEPSEEK_API = "http://localhost:20128/v1/chat/completions"  # 9Router proxy
DEEPSEEK_MODEL = "oc/deepseek-v4-flash-free"
ENRICH_MODELS = ["kc/nvidia/nemotron-3-super-120b-a12b:free", "MARK", "abelink", DEEPSEEK_MODEL]
VISION_MODEL = "gc/gemini-3.1-flash-lite-preview"  # 9Router Vision Adapter (OCR foto)
VISION_MODEL_FALLBACK = "oc/mimo-v2.5-free"

LINKEDIN_URL_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9_-]+)")

STATE_DIR = Path.home() / ".tiktok-linkedin" / "state"


def save_state(name, data):
    """Snapshot JSON bertimestamp (untuk review, bukan untuk dedup)."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = STATE_DIR / f"{name}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[save] {path}")
    return path


def _record_connections(connections: List[Dict]):
    """Merge hasil connect ke CONNECTIONS_FILE agar already_connected()
    tetap jalan antar-run (file snapshot ber-TS tidak dibaca konsumen)."""
    merged = load_json(CONNECTIONS_FILE, {})
    for c in connections:
        if c.get("result"):  # None = gagal → jangan catat, biar di-retry
            merged[c["profile"]] = {
                "handle": c["profile"],
                "state": c["result"].get("state", "Pending"),
            }
    save_json(CONNECTIONS_FILE, merged)


# ── P1: Enrich via DeepSeek ──────────────────────────────────────────────────
def extract_identities(comments: List[Dict], api_key: str) -> List[Optional[Dict]]:
    """Infer real names + companies dari komentar (batch LLM, chain fallback)."""
    import requests

    identities: List[Optional[Dict]] = []
    BATCH = 10
    for start in range(0, len(comments), BATCH):
        chunk = comments[start:start + BATCH]
        batch = [{
            "username": c["username"],
            "display_name": c["display_name"],
            "comment": c["comment_text"][:200],
        } for c in chunk]

        prompt = f"""Analyze TikTok commenters. Infer real identity.

For each:
- real_name: actual name (from display_name or context). Only if display_name looks like a real name (e.g. "John Smith"), not usernames like "funny_cat_42"
- company: employer/company if mentioned or inferable
- role: job title if mentioned
- linkedin_hint: best search query for LinkedIn
- confidence: 0-1

Commenters:
{json.dumps(batch, indent=2, ensure_ascii=False)}

Return JSON array. null if not inferable."""

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        content = None
        last_err = None
        for model in ENRICH_MODELS:
            for attempt in (1, 2):
                try:
                    resp = requests.post(DEEPSEEK_API, json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 2048,
                        "temperature": 0.1,
                        "stream": False,
                    }, headers=headers, timeout=120)
                    result = resp.json()
                    if "error" in result or "choices" not in result:
                        last_err = f"{model} (try {attempt}): {str(result.get('error', result.keys()))[:150]}"
                        print(f"[!] enrich {last_err}")
                        time.sleep(3 * attempt)
                        continue
                    content = result["choices"][0]["message"]["content"]
                    if content and content.strip():
                        break
                    last_err = f"{model} (try {attempt}): empty content"
                    print(f"[!] enrich {last_err}")
                except Exception as e:
                    last_err = f"{model} (try {attempt}): {type(e).__name__}: {e}"
                    print(f"[!] enrich {last_err}")
                    time.sleep(3 * attempt)
            if content and content.strip():
                break
        if not content or not content.strip():
            print(f"[!] enrich all models failed; last: {last_err}")
            identities.extend([None] * len(chunk))
            continue
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        try:
            parsed = json.loads(content.strip())
            if not isinstance(parsed, list):
                parsed = [parsed]
            identities.extend(parsed[:len(chunk)])
        except json.JSONDecodeError as e:
            print(f"[!] enrich parse fail: {e}; content={content[:300]!r}")
            identities.extend([None] * len(chunk))
    return identities


def extract_handle_from_image(image_url: str, api_key: str) -> Optional[str]:
    """OCR LinkedIn handle dari foto komentar via 9Router Vision Adapter."""
    import requests

    prompt = (
        "This image was posted in a TikTok comment section. It may contain a LinkedIn "
        "profile URL, QR code, or handle like https://www.linkedin.com/in/john-doe-123.\n"
        "Extract ONLY the LinkedIn public identifier (the part after linkedin.com/in/).\n"
        "Rules:\n"
        "- If you see a LinkedIn URL like linkedin.com/in/username -> return 'username'\n"
        "- If you see text like 'linkedin: johndoe' or '@johndoe' -> return 'johndoe'\n"
        "- If the image is a QR code or contains no LinkedIn identifier -> return null\n"
        "Answer with a single JSON object: {\"handle\": \"username\" or null}"
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _call(model: str) -> Optional[str]:
        resp = requests.post(DEEPSEEK_API, json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "max_tokens": 100,
            "temperature": 0.1,
            "stream": False,
        }, headers=headers, timeout=120)
        result = resp.json()
        if "error" in result:
            print(f"[vision] {model} error: {result['error']}")
            return None
        return result.get("choices", [{}])[0].get("message", {}).get("content", "")

    for model in (VISION_MODEL, VISION_MODEL_FALLBACK):
        try:
            content = _call(model)
            if not content:
                continue
            blob = content
            if "```json" in blob:
                blob = blob.split("```json")[1].split("```")[0]
            elif "```" in blob:
                blob = blob.split("```")[1].split("```")[0]
            try:
                parsed = json.loads(blob.strip())
                handle = parsed.get("handle") if isinstance(parsed, dict) else None
                if isinstance(handle, str) and handle.strip() and handle.strip().lower() != "null":
                    return handle.strip()
            except json.JSONDecodeError:
                pass
            m = LINKEDIN_URL_RE.search(content)
            if m:
                return m.group(1).rstrip("/?")
            tok = content.strip().strip('"\'.,;: ')
            if tok and tok.lower() not in ("null", "none", "no", "not found", "no handle", "-"):
                if len(tok) <= 80 and " " not in tok:
                    return tok
        except Exception as e:
            print(f"[vision] extract handle err ({model}): {e}")
    return None


# ── P3: Pipeline Orchestrator ───────────────────────────────────────────────
def _load_curated_comments(video_id: str) -> List[Dict]:
    """Baca data/curated/<today>/<video_id>.jsonl → format legacy komentar."""
    today = time.strftime("%Y-%m-%d")
    curated_file = pipeline.CURATED_DIR / today / f"{video_id}.jsonl"
    if not curated_file.exists():
        return []
    comments = []
    with curated_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # Rekonsiliasi bentuk: pipeline menulis flat dict
            # (owner disimpan sebagai EnrichedComment wrapper bila memakai dataclass)
            enriched = rec.get("enriched_data", rec)
            norm = enriched.get("normalized_data", enriched)
            comments.append({
                "username": norm.get("author_handle", ""),
                "display_name": norm.get("display_name", ""),
                "comment_text": norm.get("text_raw", ""),
                "images": norm.get("images", []),
                "comment_id": norm.get("comment_id", ""),
                "identity": enriched.get("identity"),
            })
    return comments


async def run_pipeline(
    video_url: str,
    deepseek_key: str,
    auto_connect: bool = False,
    max_scrolls: int = 20,
    max_comments: int = 100,
    no_note: bool = False,
):
    """Full pipeline: TikTok → curated corpus → LinkedIn match/connect."""

    # ── Step 1: Collect (delegated) ──
    print(f"\n{'='*60}\n  STEP 1: Collect TikTok comments (collector)\n{'='*60}\n")
    result = await collector.collect_video(
        video_url, max_scrolls=max_scrolls, max_comments=max_comments)
    if result.get("error"):
        print(f"[!] Collect gagal: {result['error']}")
        return []
    video_id = result["video_id"]

    # ── Step 2: Pipeline stages raw → curated (delegated) ──
    print(f"\n{'='*60}\n  STEP 2: Normalize → dedup → enrich → quality\n{'='*60}\n")
    pipeline.run_video(video_id)
    comments = _load_curated_comments(video_id)

    comments = cleanse_comments(comments)
    comments = comments[:max_comments]
    print(f"[dedup+cleanse] {result['comments']} raw → {len(comments)} curated")
    if not comments:
        print("[!] Tidak ada komentar lolos quality gate. Coba --scrolls lebih besar.")
        return []

    save_state("comments", comments)

    # ── Step 3: Enrich identities (LLM, consumer-level) ──
    print(f"\n{'='*60}\n  STEP 3: Extracting identities (DeepSeek)\n{'='*60}\n")
    try:
        identities = extract_identities(comments, deepseek_key)
    except Exception as e:
        print(f"[!] DeepSeek failed: {e}")
        identities = []
    identities.extend([None] * (len(comments) - len(identities)))

    for i, comment in enumerate(comments):
        comment["identity"] = identities[i] if i < len(identities) else None

    with_identity = [c for c in comments if (c.get("identity") or {}).get("real_name")]
    print(f"[enrich] {len(with_identity)} comments with identifiable names")

    v = verify_names(comments)
    print(f"[verify] {v['verified']}/{v['total']} names plausible")
    for user, why in v["issues"]:
        print(f"  ⚠ {user}: {why}")

    save_state("enriched", with_identity)

    # ── Step 4: LinkedIn Match ──
    print(f"\n{'='*60}\n  STEP 4: Searching LinkedIn\n{'='*60}\n")
    matches = []
    matched_users = set()

    for c in comments:
        m = LINKEDIN_URL_RE.search(c.get("comment_text", "") or "")
        if not m:
            continue
        handle = m.group(1).rstrip("/?")
        profile = fetch_profile(handle)
        if profile:
            matches.append({"comment": c, "identity": None, "linkedin": profile, "via": "link"})
            matched_users.add(c.get("username"))
            print(f"  ✓ [link] {handle} → {profile.get('full_name', '?')}")
        else:
            print(f"  ✗ [link] {handle} → no profile")

    photo_comments = [c for c in comments if c.get("images") and c.get("username") not in matched_users]
    for c in photo_comments:
        handle = None
        for img in c["images"][:3]:
            print(f"  👁 [photo] {c.get('username')} → OCR {img[:80]}...")
            handle = extract_handle_from_image(img, deepseek_key)
            if handle:
                break
        if not handle:
            print(f"  ✗ [photo] {c.get('username')} → no handle in image")
            continue
        profile = fetch_profile(handle)
        if profile:
            matches.append({"comment": c, "identity": None, "linkedin": profile, "via": "photo"})
            matched_users.add(c.get("username"))
            print(f"  ✓ [photo] {handle} → {profile.get('full_name', '?')}")
        else:
            print(f"  ✗ [photo] {handle} → no profile")

    for c in with_identity:
        if c.get("username") in matched_users:
            continue
        identity = c["identity"]
        name = identity.get("real_name")
        company = identity.get("company")
        if not name:
            continue

        profiles = search_linkedin(name, company)
        if profiles:
            matches.append({
                "comment": c,
                "identity": identity,
                "linkedin": profiles[0],
            })
            print(f"  ✓ {name} → {profiles[0].get('full_name', '?')}")
        else:
            print(f"  ✗ {name} → no match")
        await asyncio.sleep(1)

    print(f"\n[match] {len(matches)} LinkedIn matches")
    save_state("matches", matches)

    # ── Step 5: Connect ──
    connections = []
    if auto_connect and matches:
        print(f"\n{'='*60}\n  STEP 5: Sending connection requests\n{'='*60}\n")
        consecutive_fail = 0
        for i, match in enumerate(matches):
            if daily_conn_count() >= LINKEDIN_LIMITS["max_connections_per_day"]:
                print(f"[!] Daily limit reached (persisted: {daily_conn_count()}). Stop connecting; remaining matches saved.")
                break

            profile = match["linkedin"]
            handle = profile.get("public_identifier") or profile.get("handle")
            if not handle:
                print(f"  ✗ {profile.get('full_name')} — no handle")
                continue

            if already_connected(handle):
                print(f"  ⏭ {profile.get('full_name')} — already connected, skip")
                continue

            note = None if no_note else generate_note(profile, match["comment"])
            result_conn = send_connect(handle, note)

            if result_conn:
                consecutive_fail = 0
                connections.append({"profile": handle, "result": result_conn})
                bump_daily_conn_count()
                state = result_conn.get("state", "?")
                print(f"  {'✓' if state == 'Connected' else '⏳'} {state}: {profile.get('full_name')}" + (" (no-note)" if no_note else ""))
            else:
                consecutive_fail += 1
                connections.append({"profile": handle, "result": None})
                print(f"  ✗ Failed: {profile.get('full_name')}")

            print(f"  ⏳ {LINKEDIN_LIMITS['min_delay_between_ms']}s...")
            await asyncio.sleep(LINKEDIN_LIMITS["min_delay_between_ms"])

            if consecutive_fail >= 3:
                print(f"[!] {consecutive_fail} consecutive failures — likely LinkedIn restriction/block. Stopping connect phase; {len(matches)-i-1} matches deferred to next run.")
                break

        _record_connections(connections)
        print(f"\n[done] {len(connections)} requests sent")
    else:
        print(f"\n[review] Run with --connect to send requests.")

    # ── Step 6: CSV Report ──
    report = write_csv_report(comments, matches, connections)
    n_conn = sum(1 for c in connections if (c.get("result") or {}).get("state") == "Connected")
    n_pend = sum(1 for c in connections if c.get("result") and (c.get("result") or {}).get("state") != "Connected")
    n_fail = sum(1 for c in connections if c.get("result") is None)
    print(f"\n{'='*60}\n  SUMMARY\n{'='*60}")
    print(f"  Scraped:     {result['comments']} raw")
    print(f"  Curated:     {len(comments)}")
    print(f"  Enriched:    {len(with_identity)}")
    print(f"  Matched:     {len(matches)}")
    print(f"  Connected:   {n_conn}")
    print(f"  Pending:     {n_pend}")
    print(f"  Failed:      {n_fail}")
    print(f"  Report:      {report}")
    print("=" * 60)

    return matches


# ── CLI ──────────────────────────────────────────────────────────────────────
async def login_only():
    """Just open browser for login (collector reusable)."""
    browser = await collector.init_browser(headless=False)
    tab = browser.main_tab
    await tab.get("https://www.tiktok.com/login")
    print("\n" + "=" * 60)
    print("  Login ke TikTok manually di browser.")
    print("  Session tersimpan di ~/.tiktok-linkedin/chrome-profile/")
    print("  Tekan Ctrl+C setelah selesai.")
    print("=" * 60 + "\n")
    try:
        while True:
            await tab.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Session saved.")
    browser.stop()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="TikTok → LinkedIn pipeline")
    parser.add_argument("url", nargs="*", help="TikTok video URL(s)")
    parser.add_argument("--connect", action="store_true", help="Auto-send connection requests")
    parser.add_argument("--no-note", action="store_true", help="Connect without note")
    parser.add_argument("--max", type=int, default=300, help="Max comments")
    parser.add_argument("--scrolls", type=int, default=60, help="Max scrolls")
    parser.add_argument("--login", action="store_true", help="Login only")
    parser.add_argument("--key", help="DeepSeek API key")
    args = parser.parse_args()

    key = args.key or os.environ.get("NINEROUTER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")

    if args.login or not args.url:
        asyncio.run(login_only())
    else:
        for u in args.url:
            print(f"\n\n########## VIDEO: {u} ##########\n")
            asyncio.run(run_pipeline(
                video_url=u,
                deepseek_key=key,
                auto_connect=args.connect,
                max_scrolls=args.scrolls,
                max_comments=args.max,
                no_note=args.no_note,
            ))


if __name__ == "__main__":
    main()