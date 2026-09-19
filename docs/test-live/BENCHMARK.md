# 📊 Benchmarking — ukur stabil & error dari "zero"

> "As always don't forget to create benchmarking" — ini **high-impact functional**,
> tidak gengsi. Disebutkan di `scripts/benchmark.py`.

## Apa yang diukur (alignment dengan `improve.py`)
| Metric (di `RunMetrics`) | Definisi |
|---|---|
| `coverage` | `captured / reported` (target ≥ 0.95) |
| `captured` | berapa komentar berhasil ter-collect |
| `dup_rate` | fraksi duplikat yang dibuang |
| `avg_quality` | rata-rata `curated_score` |
| `partial` | True bila data tak lengkap / stall |
| `stall_reason` | mis `EPIPE`, `crash`, `login_required`, `no-cdp-no-camoufox` |
| `elapsed_sec` | total wall-time per video |
| `browser_source` | `cdp` (user-browser) \| `camoufox` (fresh) \| `none` |
| `n_iterations` | berapa kali improve-loop berjalan |
| `ok` | **stable** = `!partial AND coverage≥0.95 AND avg_quality≥0.35` |

## Cara pakai

## 🆚 Compare by version
Setiap record mencatat field `version = COLLECTOR_VERSION-git-describe` (mis `1.1.0-v1.1.0-dirty`). Bandingkan semua run per version:
```bash
.venv/bin/python scripts/benchmark.py --compare-versions
```
Contoh keluaran:
```
1.1.0-v1.1.0-dirty  runs=1  videos=1  pass=1  avg_cov=95.4%  avg_q=0.412  src={cdp:1}
```

```bash
# 1) Preflight (from-zero) — pastikan env bersih & tidak ada browser
./scripts/test_live.sh                          # compile+lint+scan → expect "no CDP"

# 2) Dry-run (bisa di mana saja — tidak butuh browser/login)
.venv/bin/python scripts/benchmark.py --dry-run

# 3) Live run (butuh browser CDP + login) — 2 video yang kamu tuliskan
.venv/bin/python scripts/benchmark.py --urls \
  "https://www.tiktok.com/@enxayeti/video/7669640839861112071" \
  "https://www.tiktok.com/@tiktok/video/7170139292767882522" \
  --max-comments 300 --max-scrolls 60

# 4) Dari file daftar URL (banyak)
.venv/bin/python scripts/benchmark.py --urls-file urls.txt

# 5) Ringkasan ulang dari manifest improve (tanpa scrape lagi)
.venv/bin/python scripts/benchmark.py --from-manifests data/manifests
```

## Output
Record per video → **`data/benchmarks/<run-id>.jsonl`** (satu baris JSON per video),
diikuti *summary* berisi `pass/stable rate`, `avg coverage`, `avg quality`, `avg elapsed`,
dan `src mix` (berapa % CDP vs camoufox).

Contoh baris:
```json
{"video_id":"7669640839861112071","url":"...","elapsed_sec":42.5,"browser_source":"cdp",
 "n_iterations":2,"reported":987,"captured":942,"coverage":0.954,"dup_rate":0.04,
 "avg_quality":0.412,"partial":false,"stall_reason":"","ok":true}
```

## From-zero checklist (before tiap live run)
1. `data/`, `data/manifests/`, `data/benchmarks/` bersih (atau backup lama).
2. `scripts/test_live.sh` pass sampai step 3 (scan) → terserah hasil scan.
3. Browser CDP terbuka di profil reguler + cookie login/imported.
4. Jalankan `benchmark.py --from-manifests` bila mau bandingkan with/without cookie.

> 💡 **Interpretasi hasil:** bila `browser_source: none` atau `partial: true` → berarti
> CDP gagal dan **fallback camoufox** (atau tidak ada). Bila `coverage < 0.95` &
> `stall_reason: login_required` → cookie tidak valid, *re-login / re-import*.
> Bila `dup_rate > 0.20` → bukan bug collector (lihat `improve.py` log), tapi perhatikan.
