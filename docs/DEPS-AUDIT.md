# 📦 Dependency Audit & Auto-Update

Supaya project **selalu terbaru & kebal vulnerability**, pakai dua skrip portabel
(bash/zsh untuk Linux/macOS, PowerShell untuk Windows).

## 🎯 Prinsip
- **Audit, bukan upgrade blind** — kami tidak otomatis `pip install -U` di CI (bisa bawa
  breaking change). Audit hanya **lapor**: outdated + vulnerable → *keputusan manusia*.
- **Vulnerability** ditemukan via `pip-audit` (SCA — scanning installed tree vs.
  advisory DB). Ini yang jaga *vulnerability*.
- **Stale deps** ditemukan via `pip list --outdated` + `pipdeptree`.

## 🧪 Jalankan (agent maupun human)

```bash
# Linux / macOS (bash atau zsh)
./scripts/check_deps.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File .\scripts\check_deps.ps1
```

Output tiga bagian:
1. **Outdated packages** — yang punya versi baru.
2. **Security audit** — `pip-audit`, lapor vulnerability yang terdeteksi di pohon
   dependensi (termasuk transitive).
3. **Dependency tree** — `pipdeptree` (opsional), supaya lihat dependensi mana yang
   menarik paket rentan.

## 🔄 Integrasi ke CI (`.github/workflows/ci.yml`)

Tambahkan job audit — **gagalkan build kalau ada vulnerability CRITICAL/HIGH**
(opsional) tapi jangan fail kalau cuma outdated (bisa paksa versi). Contoh patch CI:

```yaml
  deps-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: |
          pip install pip-audit
          pip-audit --strict -r requirements.txt     # fail kalau ada vuln
```

> `pip-audit -r requirements.txt` scan langsung requirements (lebih cepat).
> Gunakan `--strict` agar *fail fast* saat ada advisory.

## 🛡️ Keamanan tambahan — supply-chain
- `camoufox` berasal dari PyPI — verifikasi hash (pip `--require-hashes` di deploy produksi).
- Jalankan `pip-audit` tiap *release cycle*, bukan tiap commit (cepat di `docs/`.

## ⚙️ Env overrides
| Variable | Default | Efek |
|---|---|---|
| `PYTHON` | `.venv/bin/python` | Interpreter pakai untuk audit. |
| `CI=true` | off | (CI patch di atas otomatis aktif di CI.) |

Catatan: karena kami pakai `camoufox>=0.5.5` (bukan `>=1.0.0` — versi 1.0.0+ tidak ada
di PyPI), audit otomatis akan bilang "up to date" selama kamu pakai 0.5.5.
