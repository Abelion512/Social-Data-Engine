# 🔐 CDP Live-Test Workflow — "Act as Human, Anti-Bot"

**Tujuan:** Stabilkan koneksi CDP ke *browser yang sedang kamu pakai* (Chrome/Brave/Edge)
sehingga sistem **act as human** (fingerprint + profil + cookie asli) dan otomatis
**lewati bot-detection TikTok**. Camoufox hanya *fallback* bila tidak ada browser user.

Flow login-aware: **scan → cek login → (jika belum login → minta login manual) → CDP**.

---

## ⚠️ Syarat / Prerequisites (semua orang — agent **maupun** human)

> Baca ini **sebelum** jalankan test. Berlaku untuk **Linux, macOS, dan Windows
> (PowerShell/WSL)** — ini suport portabel. Di Windows pakai WSL (bash) atau
> PowerShell (`scripts/test_live.ps1`).

1. **Browser user sudah terbuka** (wajib) — CDP **attach** ke browser *yang sudah running*,
   tidak pernah *launch* baru tanpa izin. Kalau belum ada → jalankan flag di bawah.
2. **Profil reguler**, bukan incognito/guest — biar ada cookie/history fingerprint.
3. **.venv** aktif & `camoufox` terpasang (lihat tabel di bawah).
4. **Safety**: default `connect()` boleh fallback ke Camoufox (launch baru). Mau
   *fail-closed* (jangan pernah launch baru) → `connect(force_camoufox=False,
   allow_camoufox_fallback=False)`. `agent.py` pakai ini supaya tidak membuka browser
   baru tanpa sepengetahuan manusia.

### Cek cepat sebelum test
| Komponen | Cara cek |
|---|---|
| `.venv` aktif | `.venv/bin/python --version` → `Python 3.12.x` |
| `camoufox` terpasang | `.venv/bin/python -c "import camoufox; print(camoufox.__version__)"` |
| Playwright browsers (fallback) | `.venv/bin/python -m camoufox install` (jika belum) |
| `browser_selector` import | `.venv/bin/python -c "from src.browser_selector import BrowserSession"` |

---

## ✅ Prasyarat

| Komponen | Cara cek |
|---|---|
| `.venv` aktif | `.venv/bin/python --version` → `Python 3.12.x` |
| `camoufox` terpasang | `.venv/bin/python -c "import camoufox; print(camoufox.__version__)"` |
| Playwright browsers (fallback camoufox) | `.venv/bin/python -m camoufox install` (jika belum) |
| `tiktok_schema` import | `.venv/bin/python -c "from src.tiktok_schema import RawComment"` |

---

## 1️⃣ Buka browser user dengan CDP flag

> **Penting:** BUKAN browser *incognito* / *guest*, pakai **profil reguler** yang sudah
> punya histori (dan idealnya sudah login TikTok). Jalankan **di desktop yang sama**
> dengan tempat kamu jalankan script.

> **⚠️ ZSH BUG yang sering bikin gagal (baru saja kami temukan live-test):**
> `--remote-allow-origins=*` di **zsh** diekspansi `*` jadi *glob* → `no matches found`.
> Solusi otomatis kami: pakai **`scripts/open_browser.sh`** yang sudah quote-safe,
> auto-detect brave/chrome/edge. Kalau nulis manual, **quote `*`:** `--remote-allow-origins='*'`.

### Cara paling aman (auto-detect + zsh-safe)
```bash
cd "$(git rev-parse --show-toplevel)" 2>/dev/null || cd ~/social-data-engine  # masuk repo root; JANGAN pakai ~/... (ellipsis)
./scripts/open_browser.sh                  # detect brave/chrome, terbuka di :9222
# atau cek dulu browser apa saja tersedia:
./scripts/open_browser.sh detect
# atau paketkan launch manual (brave, flag WAJIB):
brave-browser --remote-debugging-port=9222 --remote-allow-origins='*' \
  --user-data-dir="$HOME/.tiktok-linkedin/chrome-profile" &
```

> **Brave yang sudah terbuka TANPA flag ini tidak cukup** — CDP (`--remote-debugging-port`)
> **harus diberi saat launch**. Attach ke instance biasa → scan CDP kosong → fallback
> Camoufox → `captured=0`.

### Chrome (manual, quote `*`)
```bash
google-chrome \
  --remote-debugging-port=9222 \
  --remote-allow-origins='*' \
  --user-data-dir="$HOME/.config/google-chrome/Default" &
```

### Brave (manual)
```bash
brave-browser \
  --remote-debugging-port=9222 \
  --remote-allow-origins='*' \
  --user-data-dir="$HOME/.tiktok-linkedin/chrome-profile" &
```

### Edge (manual)
```bash
microsoft-edge \
  --remote-debugging-port=9222 \
  --remote-allow-origins='*' \
  --user-data-dir="$HOME/.config/microsoft-edge/Default" &
```

> Jika sudah pakai browser lain, gunakan port lain (`9223`, … `9236`) — scanner
> deteksi otomatis memindai kelima kelompok port itu.

---

## 2️⃣ Verifikasi CDP terdeteksi + status login

```bash
cd /media/abelion/Isaf/ican/project/AGENT/mark-agent-fork/social-data-engine
.venv/bin/python -m src.browser_selector
```

Output yang diharapkan:
```
[browser] Scanning CDP browsers & login status...
[browser]  [9222] chrome — ✅ logged-in      # ← browser sudah login TikTok
# atau
[browser]  [9222] chrome — 🔄 not-logged     # ← belum login → lihat langkah 3
```

| Simbol | Artinya |
|---|---|
| `🟢/🦁/🔵 ✅ logged-in` | browser user terdeteksi + sudah login TikTok → CDP langsung dipakai |
| `🟢/🦁/🔵 🔄 not-logged` | terdeteksi tapi belum login → akan dimintai login |
| `🦊 Fallback ke Camoufox` | tidak ada browser user yang terdeteksi / login gagal |

---

## 3️⃣ Jika belum login → login manual di browser user

Jika output menampilkan `🔄 not-logged`, sistem otomatis:
1. Membuka `https://www.tiktok.com/login` **di browser user yang sedang kamu pakai** (tanpa mengganggu sesi kamu).
2. Menampilkan prompt:
   ```
   >>> tekan ENTER setelah login selesai...
   ```
3. Setelah kamu tekan `ENTER`, sistem **membaca ulang cookie** `sessionid/ttwid/uid`:
   - Jika ada → lanjut ke CDP.
   - Jika tidak → kembali ke Camoufox (anti-detect, tab baru).

> Ini cara *act as human*: **kamu** yang login manual (bukan automation), jadi fingerprint
> tetap natural & lolos bot-detection.

---

## 4️⃣ Jalankan collector

```bash
.venv/bin/python -m collector "https://www.tiktok.com/@ENXAYETI/video/7669640839861112071"
```

Log alur:
```
[browser] Scanning CDP browsers & login status...
[browser]  [9222] chrome — ✅ logged-in
[browser] ✅ 1 browser sudah login TikTok.
[agent] ✓ connected: camoufox=False   # ← CDP, bukan camoufox
```

Jika semua gagal → otomatis turun ke Camoufox (anti-detect).

---

## ⚙️ Env overrides (bisa dipasang sebelum run)

| Variable | Efek |
|---|---|
| `BROWSER_CHOICE=0` | Paksa **Camoufox** (anti-detect, tab baru). Paksa pakai ini kalau CDP sering drop. |
| `BROWSER_CHOICE=1` | Paksa pakai browser pertama terdeteksi (bypass prompt). |
| `BROWSER_CHOICE=2` | Paksa browser kedua, dst. |
| `FORCE_CAMOUFOX=1` | (collector.py) selalu pake Camoufox — berguna bila CDP nggak stabil. |

---

## 🤖 Agent mode (non-TTY) — auto cookie import

Agent biasanya **bukan via terminal**, jadi flow ini otomatis: jika tidak ada TTY,
sistem **tidak pernah panggil `input()`** — ia langsung ke **auto cookie import** → CDP,
baru jatuh ke Camoufox bila gagal.

### 1. Export cookie TikTok dari browser user
- **Cookie-Editor (Chrome extension)** → Export `.json` (format Cookie-Editor).
- **Browser built-in / uBlock** → Export `.txt` (Netscape, termasuk `#HttpOnly_`).

Simpan, mis.:
```
~/.tiktok-linkedin/tiktok_cookies.json   # JSON (Playwright / Cookie-Editor)
~/.tiktok-linkedin/tiktok_cookies.txt     # Netscape #HttpOnly_
```

### 2. Atau pakai env override (lebih eksplisit)
```bash
export TIKTOK_COOKIES="$HOME/cookies/tiktok.json"
```

### 3. Flow agent (non-TTY)
```bash
.venv/bin/python -m collector "https://tiktok.com/@user/video/ID"
# [browser] Load cookies dari ~/.tiktok-linkedin/tiktok_cookies.json ...
# [browser] 5 cookie dimuat.
# [browser] ✅ Login via cookie import berhasil!
# [agent] ✓ connected: camoufox=False      # CDP user, act as human
```
Jika tidak ada file cookie & non-TTY → otomatis **fallback Camoufox** (anti-detect,
buka tab *fresh*) — **tidak pernah blocking** agent.

### Rekuesiti API (jika dipanggil dari agent code)
```python
from src.browser_selector import BrowserSession
session = BrowserSession()
await session.connect(force_camoufox=False)
page = session.page
# session.is_cdp == True  → pakai browser user (fingerprint asli)
# session.is_camoufox == True → fallback camoufox
```

## 🛠️ Troubleshooting

| Gejala | Solusi |
|---|---|
| `_scan_with_login` tidak muncul browser apa pun | Pastikan satu browser terbuka dengan flag `--remote-debugging-port`. Tutup semua, lalu buka kembali. |
| `🔄 not-logged` terus meski sudah login | Hapus cookie TikTok lama → login ulang → tekan ENTER lagi. Kadang cookie stale. |
| `connect_over_cdp` timeout / `Connection refused` | Browser mungkin crash / port ditutup. Restart browser dengan flag di atas. |
| Firefox terdeteksi | Firefox CDP tidak kompatibel dengan chromium bridge → **otomatis fallback Camoufox**. Pakai Chrome/Brave/Edge untuk hasil terbaik. |
| `camoufox fallback gagal` (headless server) | `.venv/bin/python -m camoufox install` dulu supaya binary Firefox tersedia. |
| `BROWSER_CHOICE` tidak efek | Pastikan tidak override lagi di terminal. `unset BROWSER_CHOICE`. |

---

## 🧪 Mini checklist verifikasi akhir (jalanin sekali)

```bash
# 1. compile
.venv/bin/python -m compileall -q src/ scripts/

# 2. import smoke
.venv/bin/python -c "import importlib,sys; sys.path.insert(0,'.'); \
[importlib.import_module(m) for m in ['src.browser_selector','src.harness','scripts.camoufox_collector']]; print('imports OK')"

# 3. pyflakes (harus 0 fatal)
.venv/bin/python -m pyflakes src/ scripts/ 2>&1 | grep -ciE "undefined name|cannot be resolved|No module|assigned to but never"

# 4. live scan (harus lihat browsermu)
.venv/bin/python -m src.browser_selector
```
