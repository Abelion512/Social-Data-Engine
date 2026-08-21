#!/usr/bin/env python3
"""
Browser Selector — Smart browser detection + CDP connect + Camoufox fallback.

Alur (prioritas):
  1. Scan port CDP (9222-9236) → detect Chrome/Brave/Edge/Firefox yang running
  2. Kalau >1 browser: tanya user pakai yang mana
  3. CDP connect via Playwright connect_over_cdp()
  4. Kalau CDP gagal: fallback ke Camoufox (anti-detect)

Kenapa CDP sering gagal?
  - Chrome butuh flag: --remote-debugging-port=9222 --remote-allow-origins=*
  - Brave/Edge kadang pakai port berbeda
  - Profile directory locked oleh browser instance lain
  - Firewall/permission blocking localhost connection
"""
from __future__ import annotations
import os
import asyncio
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    async_playwright = None  # type: ignore

from camoufox.async_api import AsyncCamoufox

PROFILE_DIR = Path.home() / ".tiktok-linkedin" / "chrome-profile"


@dataclass
class DetectedBrowser:
    """Hasil deteksi browser dari CDP."""
    port: int
    title: str          # dari /json/version → Browser: "Chrome/120.0..."
    ws_endpoint: str    # webSocketDebuggerUrl
    user_data_dir: str  # dari /json (profile path)
    browser_type: str   # "chrome", "brave", "edge", "firefox", "chromium", "unknown"


def _http_get_json(url: str, timeout: float = 1.0) -> Optional[dict]:
    """GET request → JSON response. Sync wrapper via urllib (stdlib)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def _http_get_json_list(url: str, timeout: float = 1.0) -> List[dict]:
    """GET request → JSON array. Sync wrapper via urllib (stdlib)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _check_cdp_port(port: int, timeout: float = 1.0) -> Optional[dict]:
    """Cek apakah port CDP aktif. Returns /json/version response atau None."""
    return _http_get_json(f"http://127.0.0.1:{port}/json/version", timeout)


def _get_browser_tabs(port: int, timeout: float = 1.0) -> List[dict]:
    """Ambil daftar tab dari CDP endpoint."""
    return _http_get_json_list(f"http://127.0.0.1:{port}/json", timeout)


def _classify_browser(version_str: str) -> str:
    """Classify browser type dari version string."""
    v = version_str.lower()
    if "brave" in v:
        return "brave"
    elif "edge" in v or "edg/" in v:
        return "edge"
    elif "firefox" in v:
        return "firefox"
    elif "chrome" in v:
        return "chrome"
    elif "chromium" in v:
        return "chromium"
    return "unknown"


def detect_browsers(ports: Optional[List[int]] = None) -> List[DetectedBrowser]:
    """Scan CDP ports dan detect running browsers.

    Default scan: 9222-9236 (port umum Chrome/Brave/Edge/Firefox).
    Sync function — panggil dari sync context atau asyncio.run().
    """
    if ports is None:
        ports = list(range(9222, 9237))  # 9222-9236

    detected: List[DetectedBrowser] = []

    for port in ports:
        version_info = _check_cdp_port(port, timeout=0.5)
        if not version_info:
            continue

        browser_title = version_info.get("Browser", "")
        ws_endpoint = version_info.get("webSocketDebuggerUrl", "")
        browser_type = _classify_browser(browser_title)

        # Ambil tab info
        tabs = _get_browser_tabs(port, timeout=0.5)
        user_data_dir = ""
        if tabs:
            tab = tabs[0]
            user_data_dir = tab.get("devtoolsFrontendUrl", "")

        detected.append(DetectedBrowser(
            port=port,
            title=browser_title,
            ws_endpoint=ws_endpoint,
            user_data_dir=user_data_dir,
            browser_type=browser_type,
        ))

    return detected


async def _detect_browsers_async(ports: Optional[List[int]] = None) -> List[DetectedBrowser]:
    """Async wrapper untuk detect_browsers()."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, detect_browsers, ports)


def _promt_user_browser(detected: List[DetectedBrowser]) -> Optional[DetectedBrowser]:
    """Tampilkan opsi browser ke user dan minta pilihan. Sync input()."""
    icons = {
        "chrome": "🟢", "brave": "🦁", "edge": "🔵",
        "firefox": "🦊", "chromium": "⚪", "unknown": "❓",
    }

    print("\n" + "=" * 60)
    print("  🌐 Browser terdeteksi via CDP:")
    print("=" * 60)

    for i, b in enumerate(detected, 1):
        icon = icons.get(b.browser_type, "❓")
        print(f"  [{i}] {icon} {b.title}")
        print(f"      Port: {b.port} | Type: {b.browser_type}")
        print()

    print("  [0] 🦊 Gunakan Camoufox (anti-detect, buka browser baru)")
    print("=" * 60)

    while True:
        try:
            choice = input(f"  Pilih browser (0-{len(detected)}): ").strip()
            idx = int(choice)
            if idx == 0:
                return None  # Camoufox
            if 1 <= idx <= len(detected):
                return detected[idx - 1]
            print(f"  ⚠️  Pilih 0-{len(detected)}")
        except (ValueError, EOFError):
            print("  ⚠️  Input tidak valid, coba lagi")
        except KeyboardInterrupt:
            print("\n  Batal.")
            sys.exit(0)


async def _connect_cdp(detected: DetectedBrowser) -> Tuple[Optional[Browser], Optional[BrowserContext], Optional[Page]]:
    """Connect ke user browser via CDP using Playwright."""
    if async_playwright is None:
        print("[browser] ⚠️  playwright tidak terinstall — pip install playwright")
        return None, None, None

    print(f"[browser] Connecting ke {detected.title} di port {detected.port}...")

    try:
        pw = await async_playwright().start()

        browser = await pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{detected.port}",
            timeout=10000,
        )

        contexts = browser.contexts
        if contexts:
            context = contexts[0]
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = await context.new_page()
        else:
            context = await browser.new_context()
            page = await context.new_page()

        print(f"[browser] ✅ Connected ke {detected.title}")
        try:
            title = await page.title()
            print(f"[browser]    Tab: {title}")
        except Exception:
            pass
        return browser, context, page

    except Exception as e:
        error_msg = str(e)
        print(f"[browser] ❌ CDP connect gagal: {error_msg[:120]}")

        if "refused" in error_msg.lower():
            print("[browser]    💡 Browser belum di-start dengan --remote-debugging-port=")
            bt = detected.browser_type
            if bt == "brave":
                print(f"[browser]    💡 Coba: brave-browser --remote-debugging-port={detected.port} --remote-allow-origins=*")
            elif bt == "edge":
                print(f"[browser]    💡 Como: microsoft-edge --remote-debugging-port={detected.port} --remote-allow-origins=*")
            elif bt == "firefox":
                print(f"[browser]    💡 Coba: firefox --remote-debugging-port={detected.port}")
            else:
                print(f"[browser]    💡 Coba: google-chrome --remote-debugging-port={detected.port} --remote-allow-origins=*")
        elif "timeout" in error_msg.lower():
            print("[browser]    💡 Koneksi timeout — firewall atau port salah")
        elif "origin" in error_msg.lower():
            print("[browser]    💡 Tambahan --remote-allow-origins=* diperlukan")
        else:
            print(f"[browser]    💡 Error detail: {error_msg[:200]}")

        return None, None, None


async def _open_camoufox():
    """Buka Camoufox sebagai fallback."""
    print("[browser] 🦊 Fallback ke Camoufox (anti-detect Firefox)...")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        for f in PROFILE_DIR.glob("Singleton*"):
            f.unlink()
    except Exception:
        pass

    # persistent_context=True is required: camoufox/playwright only accepts
    # `user_data_dir` on launch_persistent_context (not plain launch).
    # Discovered via live test (TypeError: BrowserType.launch() got an
    # unexpected keyword argument 'user_data_dir').
    # headless: default False (visible — keeps human-in-the-loop safety on
    # desktop). Set CAMOUFOX_HEADLESS=true to run headless (e.g. on a headless
    # server / CI / xvfb-free). Discovered via live-test: headless=False with
    # no display left the run parked at browser-warmup; headless=True boots &
    # reaches TikTok on a headless server.
    headless = os.environ.get("CAMOUFOX_HEADLESS", "false").lower() not in (
        "0", "false", "no", ""
    )
    browser_cm = AsyncCamoufox(
        headless=headless,
        user_data_dir=str(PROFILE_DIR),
        persistent_context=True,
    )
    cm = await browser_cm.__aenter__()
    page = await cm.new_page()
    return browser_cm, cm, page


class BrowserSession:
    """Wrapper untuk browser session — bisa CDP atau Camoufox."""

    def __init__(self):
        self.browser = None
        self.page = None
        self._cm = None
        self.is_cdp = False
        self.is_camoufox = False

    async def connect(self, force_camoufox: bool = False) -> bool:
        """Connect ke browser. Returns True jika berhasil.

        Priority: user browser (CDP) → Camoufox fallback.
        """
        if force_camoufox:
            # Wrap launch in try/except: if the firefox binary isn't cached
            # (headless server) or AsyncCamoufox fails, we must NOT crash the
            # caller — return False so `if not await session.connect(...)` can
            # short-circuit cleanly. (Found via live-test: the old unconditional
            # `return True` let an uncaught TypeError propagate into login_only
            # and collect_video.)
            try:
                self.browser, self._cm, self.page = await _open_camoufox()
            except Exception as e:
                print(f"[browser] ❌ Camoufox launch gagal: {e}")
                return False
            if self.page is None:
                print("[browser] ❌ Camoufox gagal — tidak ada halaman browser")
                return False
            self.is_camoufox = True
            return True

        # Step 1: Scan CDP browsers + cek login TikTok tiap browser.
        # Act-as-human / anti-bot: prioritaskan browser user yang SUDAH login
        # (fingerprint + profil asli). Kita ATTACH (bukan launch) sehingga
        # memakai session/cookie user yang ada — itu keunggulan anti-detect
        # dibanding camoufox (profil "baku").
        print("[browser] Scanning CDP browsers & login status...")
        scan = await _scan_with_login()

        if not scan:
            print("[browser] Tidak ada browser CDP yang terdeteksi.")
            print("[browser] 💡 Untuk pakai browser user, start dengan CDP flag:")
            print("[browser]    Chrome:  google-chrome --remote-debugging-port=9222 --remote-allow-origins=*")
            print("[browser]    Brave:   brave-browser --remote-debugging-port=9222 --remote-allow-origins=*")
            print("[browser]    Edge:    microsoft-edge --remote-debugging-port=9222 --remote-allow-origins=*")
            print("[browser]    Firefox: firefox --remote-debugging-port=9222")
            print()
            print("[browser] 🦊 Fallback ke Camoufox...")
            self.browser, self._cm, self.page = await _open_camoufox()
            self.is_camoufox = True
            return True

        # chromium-family browsers yang sudah login TikTok
        logged_in = [s for s in scan if s["logged_in"] and _cdp_browser_type(s["browser"])]
        # chromium-family yang belum login (kandidat untuk dimintai login)
        user_browsers = [s for s in scan if not s["logged_in"] and _cdp_browser_type(s["browser"])]

        if logged_in:
            print(f"[browser] ✅ {len(logged_in)} browser sudah login TikTok.")
            chosen = _choose_browser_entry(logged_in, prefer_logged_in=True)
            if chosen is not None:
                browser, ctx, page = await _connect_cdp(chosen["browser"])
                if browser:
                    self.browser = browser
                    self.page = page
                    self.is_cdp = True
                    return True
        else:
            # Belum ada yang login → minta user pilih browser, lalu login dulu.
            print("[browser] Belum ada browser yang login TikTok.")
            candidates = user_browsers or scan  # paksa pakai chromium bila ada
            if any(_cdp_browser_type(s["browser"]) for s in candidates):
                candidates = [s for s in candidates if _cdp_browser_type(s["browser"])]
            pick = _choose_browser_entry(candidates, force=True)
            if pick is not None:
                ok = await _prompt_user_login(pick["browser"])
                if ok:
                    browser, ctx, page = await _connect_cdp(pick["browser"])
                    if browser:
                        self.browser = browser
                        self.page = page
                        self.is_cdp = True
                        return True
            print("[browser] Login gagal / tidak ada browser user yang dipilih.")

        # CDP gagal atau tidak ada browser user login → fallback Camoufox
        print("[browser] 🦊 Fallback ke Camoufox (anti-detect)...")
        try:
            self.browser, self._cm, self.page = await _open_camoufox()
            if self.page is None:
                return False
            self.is_camoufox = True
            return True
        except Exception as e:
            print(f"[browser] ❌ Camoufox fallback gagal: {e}")
            return False

    async def close(self):
        """Cleanup browser session."""
        try:
            if self.is_cdp and self.browser:
                await self.browser.close()
            elif self.is_camoufox and self._cm:
                await self._cm.__aexit__(None, None, None)
        except Exception as e:
            print(f"[browser] Cleanup warning: {e}")

    def __repr__(self):
        mode = "CDP" if self.is_cdp else "Camoufox" if self.is_camoufox else "None"
        return f"<BrowserSession mode={mode}>"


# ── TikTok login awareness (CDP) ──────────────────────────────────────────────
TIKTOK_URL = "https://www.tiktok.com"
# Cookie names TikTok pakai untuk session/identity. Jika ada salah satu di
# context user browser → dianggap sudah login.
_TIKTOK_SESSION_COOKIE_NAMES = {
    "sessionid", "sessionid_v2", "ttwid", "ttwid_v2", "ttwid_4",
    "uid", "sid_tt", "sidr", "s_vid",
}


def _cdp_browser_type(detected: "DetectedBrowser") -> str:
    """Map detected browser_type → playwright browser group for CDP connect.

    CDP connect_over_cbp() hanya langsung work di Chromium-family
    (Chrome/Brave/Edge/Chromium). Firefox CDP pakai protocol berbeda dan
    tidak kompatibel dengan chromium.connect_over_cdp — untuk itu kita
    me-lewati CDP (return '') dan biarkan fallback ke Camoufox.
    """
    if detected.browser_type in ("chrome", "brave", "edge", "chromium"):
        return "chromium"
    return ""


async def _read_tiktok_session(detected: "DetectedBrowser", timeout: float = 10.0) -> bool:
    """Attach ke user browser via CDP, baca cookie context UNTUK login TikTok.

    Penting (act-as-human & anti-detect):
      - **attach** via connect_over_cdp (bukan launch baru) → memakai profil/cookie
        user yang asli (fingerprint natural, history, login state). Itu justru
        *anti bot detection* terbaik: kita jadi "user biasa" yang browser-nya udah login.
      - **disconnect** (bukan close) → browser user tetap hidup, tidak ganggu interaksi user.
      - JANGAN navigate / klik apa-apa — cukup baca cookie context yang ada.

    Returns True bila ada cookie session TikTok di context pertama.
    """
    if async_playwright is None:
        return False
    if not _cdp_browser_type(detected):
        return False  # firefox/unsupported → lewati CDP
    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{detected.port}", timeout=int(timeout * 1000)
        )
        contexts = browser.contexts
        # context user pertama = profil yang sedang dipakai
        ctx = contexts[0] if contexts else await browser.new_context()
        try:
            cookies = await ctx.cookies()
        except Exception:
            cookies = []
        for c in cookies:
            name = (c.get("name") or "").lower()
            if any(s in name for s in ("sessionid", "ttwid", "uid", "sid")):
                return True
        return False
    except Exception as e:
        print(f"[browser] CDP login-check gagal {detected.title}: {str(e)[:80]}")
        return False
    finally:
        # Detach (jangan close!) supaya browser user tetap hidup.
        if browser is not None:
            try:
                await browser.disconnect()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass


async def _scan_with_login() -> list:
    """Detect CDP browsers + cek login TikTok masing-masing.

    Returns list of dict: {browser, port, type, title, logged_in}.
    Hanya chromium-family yang dicek login (firefox → dilewati, fallback camoufox).
    """
    detected = detect_browsers()
    out = []
    for b in detected:
        li = False
        if _cdp_browser_type(b):
            li = await _read_tiktok_session(b)
        out.append({
            "browser": b, "port": b.port, "type": b.browser_type,
            "title": b.title, "logged_in": li,
        })
        tag = "✅ logged-in" if li else "🔄 not-logged"
        print(f"[browser]  [{b.port}] {b.browser_type} — {tag}")
    return out


async def _prompt_user_login(detected: "DetectedBrowser") -> bool:
    """Human-in-the-loop: minta user login di browser, lalu konfirmasi siap.

    Alur (act-as-human):
      1. Buka halaman login TikTok di context user (via CDP attach).
      2. Tunjukkan link login + instruksi.
      3. User tekan Enter di terminal setelah login selesai.
      4. Kita cek cookie sessionid kembali → True bila ada.
    """
    print("\n" + "=" * 60)
    print(f"  🔐 Browser terpilih: {detected.title} (port {detected.port})")
    print("  Silakan login ke TikTok di browser kamu:")
    print(f"    {TIKTOK_URL}/login")
    print("  Setelah berhasil login (ada profil/avatar kamu), kembali ke sini")
    print("  dan tekan ENTER.")
    print("=" * 60)

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{detected.port}", timeout=15000
        )
        contexts = browser.contexts
        ctx = contexts[0] if contexts else await browser.new_context()
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(f"{TIKTOK_URL}/login", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            input(">>> tekan ENTER setelah login selesai... ")
        except EOFError:
            pass
        # re-check cookie setelah user konfirmasi
        return await _read_tiktok_session(detected)
    finally:
        if browser is not None:
            try:
                await browser.disconnect()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass


def _choose_browser_entry(entries: list, force: bool = False,
                         prefer_logged_in: bool = False) -> Optional[dict]:
    """Pilih satu entry dari daftar scan (dict dengan key 'browser' + 'logged_in').

      - TTY (manusia) + >1 : tampilkan opsi, minta pilih.
      - non-TTY (agent)    : auto-pick; `BROWSER_CHOICE` env override.
      - 1 entry           : langsung pakai.
      - `force=True`      : selalu tampilkan pilihan walaupun 1 entry (untuk login-prompt).
    """
    if not entries:
        return None

    # env override (nomor 1..N → browser ke-i; 0 → camoufox/None)
    env_choice = os.environ.get("BROWSER_CHOICE", "").strip()
    if env_choice.isdigit():
        idx = int(env_choice)
        if idx == 0:
            return None
        if 1 <= idx <= len(entries):
            return entries[idx - 1]

    if force or (_is_interactive() and len(entries) > 1):
        print("\n  🌐 Browser terdeteksi:")
        icons = {"chrome": "🟢", "brave": "🦁", "edge": "🔵",
                 "firefox": "🦊", "chromium": "⚪", "unknown": "❓"}
        for i, e in enumerate(entries, 1):
            b = e["browser"]
            icon = icons.get(b.browser_type, "❓")
            tag = " ✅ logged-in" if e.get("logged_in") else " 🔄 not-logged"
            print(f"  [{i}] {icon} {b.title} — port {b.port} ({b.browser_type}){tag}")
        print("  [0] 🦊 Camoufox (anti-detect, tab buka baru)")
        try:
            choice = input(f"  Pilih browser (0-{len(entries)}): ").strip()
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= len(entries):
                return entries[idx - 1]
        except (ValueError, EOFError, KeyboardInterrupt):
            print("  ⚠️  Input tidak valid → auto-pick.")
    # auto-pick (single entry / non-interactive / after-bad-input).
    # entries sudah diurutkan: yang paling cocok (logged-in / user browser) di depan.
    return entries[0]
# ── Quick test ─────────────────────────────────────────────────────────────────
def _test_detect_sync():
    """Quick test untuk browser detection (sync)."""
    print("Scanning CDP ports 9222-9236...")
    detected = detect_browsers()
    if not detected:
        print("Tidak ada browser CDP yang terdeteksi.")
        print("Jalankan Chrome dengan: --remote-debugging-port=9222 --remote-allow-origins=*")
    else:
        icons = {"chrome": "🟢", "brave": "🦁", "edge": "🔵", "firefox": "🦊", "chromium": "⚪"}
        for b in detected:
            icon = icons.get(b.browser_type, "❓")
            print(f"  {icon} [{b.port}] {b.title} ({b.browser_type})")


def _is_interactive() -> bool:
    """Apakah interpreter ini terhubung ke TTY (interaktif manusia)?
    Agent/CI yang mengarahkan stdin ke non-TTY -> False -> auto-pick."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _prefer_user_browser(detected: List[DetectedBrowser]) -> Optional[DetectedBrowser]:
    """Utamakan browser *user* yang sedang berjalan (Chrome/Brave/Edge/Chromium)."""
    for b in detected:
        if b.browser_type in ("chrome", "brave", "edge", "chromium"):
            return b
    return detected[0] if detected else None


def _print_detected(detected: List[DetectedBrowser]) -> None:
    icons = {"chrome": "🟢", "brave": "🦁", "edge": "🔵",
             "firefox": "🦊", "chromium": "⚪", "unknown": "❓"}
    if not detected:
        print("Tidak ada browser CDP yang terdeteksi. Gunakan Camoufox atau "
              "start browser dengan --remote-debugging-port=9222")
        return
    for b in detected:
        icon = icons.get(b.browser_type, "❓")
        print(f"  {icon} [{b.port}] {b.title} ({b.browser_type})")


def select_browser(detected: List[DetectedBrowser], interactive: Optional[bool] = None) -> Optional[DetectedBrowser]:
    """Pemilihan browser: auto-detect dulu, lalu:
      - **Manusia** (TTY): tampilkan opsi + minta pilih.
      - **Agent / non-TTY**: auto-pick browser user (atau paksa via env `BROWSER_CHOICE`).
      - `BROWSER_CHOICE=0` -> paksa Camoufox (fallback anti-detect).
    Kembalikan DetectedBrowser, atau None -> gunakan Camoufox.
    """
    if interactive is None:
        interactive = _is_interactive()

    env_choice = os.environ.get("BROWSER_CHOICE", "").strip()
    if env_choice.isdigit():
        idx = int(env_choice)
        if idx == 0:
            return None  # Camoufox
        if 1 <= idx <= len(detected):
            return detected[idx - 1]

    if interactive and len(detected) > 1:
        # manusia interaktif -> tanya pilih
        return _promt_user_browser(detected)

    # non-interactive (agent) OR single browser -> auto, prioritize user browser
    return _prefer_user_browser(detected)


async def _test_detect():
    """Async wrapper — `collector.py --detect` imports `_test_detect` (bukan
    `_test_detect_sync`). Async agar `asyncio.run(_test_detect())` valid."""
    loop = asyncio.get_event_loop()
    detected = await loop.run_in_executor(None, detect_browsers)
    _print_detected(detected)


if __name__ == "__main__":
    _test_detect_sync()
