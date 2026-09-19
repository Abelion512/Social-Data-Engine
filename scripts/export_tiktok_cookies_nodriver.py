#!/usr/bin/env python3
"""Export TikTok cookies from the real Chrome profile (nodriver) to JSON for Mark's Electron session import.

TM-19 (asset handling): the export is written 0600/dir 0700 via
``src.runtime.context.write_private_text`` — it contains live session tokens.
"""
import asyncio
import json
import sys
from pathlib import Path

import nodriver as uc

# Repo root on sys.path — needed when run as `python scripts/export_tiktok_cookies_nodriver.py`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.runtime.context import write_private_text  # noqa: E402

PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
OUT = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"


def ser(c):
    ss = getattr(c, "same_site", None)
    try:
        ss_val = ss.value if ss is not None else None
    except Exception:
        ss_val = None
    if ss_val == "None":
        ss_val = "no_restriction"
    elif ss_val:
        ss_val = ss_val.lower()
    exp = getattr(c, "expires", -1)
    return {
        "name": c.name,
        "value": c.value,
        "domain": c.domain,
        "path": c.path or "/",
        "secure": bool(getattr(c, "secure", True)),
        "httpOnly": bool(getattr(c, "http_only", False)),
        "sameSite": ss_val or "no_restriction",
        "expirationDate": None if exp in (-1, None) else float(exp),
    }


async def main():
    browser = await uc.start(user_data_dir=str(PROFILE_DIR), headless=True)
    try:
        tab = await browser.get("https://www.tiktok.com")
        await tab.sleep(4)
        cookies = await browser.cookies.get_all()
        tk = [c for c in cookies if "tiktok.com" in (c.domain or "")]
        names = [c.name for c in tk]
        print("TIKTOK_COOKIES:", len(tk))
        print("NAMES:", ",".join(names))
        if not any(n in ("sessionid", "sid_tt", "sessionid_ss") for n in names):
            print("LOGIN_STATE: NOT_LOGGED_IN")
        else:
            print("LOGIN_STATE: LOGGED_IN")
            write_private_text(OUT, json.dumps([ser(c) for c in tk], indent=1))
            print("SAVED:", OUT, "(mode 0600 — never commit or share this file)")
    finally:
        browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
