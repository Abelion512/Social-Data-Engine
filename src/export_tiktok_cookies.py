#!/usr/bin/env python3
"""Export TikTok cookies from Camoufox profile to JSON for session reuse.

TM-19 (asset handling): the exported file holds live session credentials, so
it is written with owner-only permissions (0600, dir 0700) via
``src.runtime.context.write_private_text`` instead of a world-readable default
``write_text``.
"""
import asyncio
import json
import sys
from pathlib import Path

from camoufox.async_api import AsyncCamoufox

# Repo root on sys.path — needed when run as `python src/export_tiktok_cookies.py`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.runtime.context import write_private_text  # noqa: E402

PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
OUT = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"


async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(headless=True, user_data_dir=str(PROFILE_DIR)) as browser:
        page = await browser.new_page()
        await page.goto("https://www.tiktok.com", wait_until="commit", timeout=30000)
        await asyncio.sleep(4)
        cookies = await page.context.cookies("https://www.tiktok.com")
        tk = [c for c in cookies if "tiktok.com" in (c.get("domain", "") or "")]
        names = [c["name"] for c in tk]
        print(f"TIKTOK_COOKIES: {len(tk)}")
        print(f"NAMES: {','.join(names)}")
        if not any(n in ("sessionid", "sid_tt", "sessionid_ss") for n in names):
            print("LOGIN_STATE: NOT_LOGGED_IN")
        else:
            print("LOGIN_STATE: LOGGED_IN")
            write_private_text(OUT, json.dumps(tk, indent=1))
            print(f"SAVED: {OUT} (mode 0600 — never commit or share this file)")


if __name__ == "__main__":
    asyncio.run(main())
