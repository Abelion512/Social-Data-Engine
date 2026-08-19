#!/usr/bin/env python3
"""Export TikTok cookies from Camoufox profile to JSON for session reuse."""
import asyncio
import json
from pathlib import Path

from camoufox.async_api import AsyncCamoufox

PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"
OUT = Path.home() / ".tiktok-linkedin" / "tiktok-cookies.json"


def serialize_cookie(c) -> dict:
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
            OUT.write_text(json.dumps(tk, indent=1))
            print(f"SAVED: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
