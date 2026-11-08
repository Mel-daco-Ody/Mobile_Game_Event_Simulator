# Hướng Dẫn Sử Dụng – Mobile Game Event Simulator

Tài liệu này hướng dẫn cách cài đặt, cấu hình, chạy và verify project
**GAP_v2.0 – Mobile Game Event Simulator** (một pipeline Python sinh dữ liệu
game events mô phỏng, gửi tới GameAnalytics Collection API hoặc ghi raw
data local).

> Tài liệu đặc tả đầy đủ (kiến trúc, schema, metrics) xem `[README.md](./README.md)`.

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt](#2-cài-đặt)
3. [Cấu hình credentials GameAnalytics](#3-cấu-hình-credentials-gameanalytics)
4. [Cấu trúc project](#4-cấu-trúc-project)
5. [Chạy simulation (main pipeline)](#5-chạy-simulation-main-pipeline)
6. [Generate UA Spend dataset](#6-generate-ua-spend-dataset)
7. [Smoke tests](#7-smoke-tests)
8. [Output: report, SQLite, raw files](#8-output-report-sqlite-raw-files)
9. [Các tham số CLI đầy đủ](#9-các-tham-số-cli-đầy-đủ)
10. [Troubleshooting](#10-troubleshooting)

---



## 1. Yêu cầu hệ thống


| Thành phần | Yêu cầu                                                                        |
| ---------- | ------------------------------------------------------------------------------ |
| Python     | 3.11+ (tested trên 3.11, 3.12)                                                 |
| OS         | Windows 10/11, macOS, Linux                                                    |
| RAM        | ≥ 2 GB cho run ≤ 100k players × 30 ngày                                        |
| Disk       | Vài GB cho raw data lake                                                       |
| Network    | Cần internet nếu gửi events thật tới GameAnalytics (không cần nếu `--dry-run`) |


Phụ thuộc Python (đã có sẵn trong môi trường):

- `requests`, `urllib3`, `python-dateutil`
- KHÔNG cần `python-dotenv` (tự parse `.env`)
- KHÔNG cần C compiler hay native deps

---



## 2. Cài đặt



### 2.1. Clone và setup

```bash
cd c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj
# (Hoặc repo path tương ứng)
```

Không cần `pip install -r requirements.txt` – project dùng stdlib + một vài package đã có sẵn trong môi trường Anaconda/Miniconda.

### 2.2. Kiểm tra Python và dependencies

```powershell
python --version    # Python 3.11+
python -c "import requests, sqlite3, gzip, json; print('OK')"
```



### 2.3. Đảm bảo UTF-8 cho stdout (Windows)

Project đã tự set trong `main.py` qua `PYTHONIOENCODING=utf-8`. Nếu gặp lỗi tiếng Việt:

```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

---



## 3. Cấu hình credentials GameAnalytics



### 3.1. Lấy GAME_KEY và SECRET_KEY

1. Đăng ký game tại [https://gameanalytics.com](https://gameanalytics.com)
2. Vào **Game Settings** → copy `GAME_KEY` và `SECRET_KEY`



### 3.2. Tạo file `.env`

Project đã có sẵn file `prj/.env` (gitignored). Mở và điền:

```ini
# === GameAnalytics credentials ===
GAME_KEY=your_game_key_here
SECRET_KEY=your_secret_key_here
GA_ENV=sandbox                # 'sandbox' | 'production'

# (Tùy chọn) SQL Server credentials – chỉ cần nếu dùng SSMS storage
# SSMS_SQL_SERVER_HOST=localhost
# SSMS_SQL_SERVER_PORT=1433
# ...
```

> **Lưu ý bảo mật**: KHÔNG commit `.env` vào git. Project đã có trong `.gitignore`.



### 3.3. Các chế độ chạy


| Chế độ         | Flag                  | Hành vi                                                 |
| -------------- | --------------------- | ------------------------------------------------------- |
| **Dry-run**    | `--dry-run`           | Chỉ ghi raw data + SQLite, KHÔNG POST tới GameAnalytics |
| **Sandbox**    | (default)             | Gửi tới GameAnalytics sandbox environment               |
| **Production** | `--ga-env production` | Gửi tới GameAnalytics production (cần credentials thật) |


Nếu không có credentials → tự động fallback sang `--dry-run` (có warning log).

---



## 4. Cấu trúc project

```
GAP_v2.0/
├── README.md                     # Spec đầy đủ (kiến trúc, schema, 25 bước)
├── USAGE.md                      # File này
└── prj/
    ├── main.py                   # Entry point CLI chính
    ├── .env                      # Credentials (gitignored)
    ├── simulator.db              # SQLite state (auto-generated)
    ├── data/
    │   ├── raw/                  # Raw data lake (partitioned year/month/day)
    │   ├── report.json           # Simulation report JSON
    │   └── ua_spend/
    │       ├── README.md         # UA spend dataset docs
    │       └── ua_spend.csv      # UA spend dataset
    ├── config/                   # Configuration modules
    │   ├── credentials.py        # .env loader
    │   ├── game_config.py        # Countries, platforms, devices
    │   ├── player_config.py      # Player segments + weights
    │   ├── monetization_config.py# IAP catalog, ECPM
    │   ├── retention_config.py   # Retention curves
    │   └── ua_spend_config.py    # UA spend config
    ├── models/                   # Data classes (Player, Session, ...)
    ├── generators/               # Event generators (player, business, ad, ...)
    │   └── smoke_test_*.py       # Per-module smoke tests
    ├── gameanalytics/            # GA client + auth + batcher + validator
    │   └── smoke_test_*.py
    ├── storage/                  # SQLite + raw writer + queue
    │   └── smoke_test_*.py
    └── simulation/               # DailyRunner, SimulationRunner, ReportBuilder
        ├── daily_runner.py       # Core simulation engine
        ├── report.py             # Report data classes
        ├── ua_spend.py           # UA spend generator
        ├── generate_ua_spend.py  # CLI cho UA spend
        ├── smoke_test_e2e.py     # E2E smoke test (8 phases)
        ├── smoke_test_main.py    # Smoke test cho main pipeline
        ├── smoke_test_retention.py
        └── smoke_test_ua_spend.py
```

---



## 5. Chạy simulation (main pipeline)



### 5.1. Quick start (dry-run, 1000 players × 7 ngày)

```powershell
cd c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj
python main.py --players 1000 --days 7 --seed 42 --dry-run
```

**Output** (~ vài giây):

```
[STEP 1] Load configuration
[STEP 2] Load GameAnalytics credentials (.env)
[STEP 3] Initialize SQLite database: simulator.db
[STEP 4] Generate players (count=1000, seed=42)
[STEP 5] Build pipeline (Batcher + Validator + RawWriter + Client)
[STEP 6] Run simulation: 7 days from 2026-09-29
[STEP 7] Save report: data/report.json
...
KẾT QUẢ SIMULATION
  Days              : 7
  Players           : 1000
  Sessions          : ~5000
  Events            : ~50000
  ...
```



### 5.2. Production-like (gửi tới GameAnalytics sandbox)

```powershell
python main.py --players 5000 --days 14 --seed 42 --ga-env sandbox
```

> Cần có `.env` với `GAME_KEY` + `SECRET_KEY` hợp lệ.



### 5.3. Large-scale run

```powershell
python main.py --players 100000 --days 30 --seed 42 --dry-run
```

- Dung lượng raw data: ~1–3 GB (gzip)
- Thời gian: ~5–15 phút (phụ thuộc CPU)
- SQLite có thể lớn tới ~500 MB → cân nhắc dùng SQL Server



### 5.4. Custom start date

```powershell
python main.py --players 1000 --days 7 --start-date 2026-10-01 --dry-run
```



### 5.5. Reset DB (xóa players/sessions cũ)

```powershell
python main.py --players 1000 --days 7 --init-db --dry-run
```



### 5.6. Custom output paths

```powershell
python main.py `
    --players 5000 `
    --days 14 `
    --seed 42 `
    --db my_run/sim.db `
    --raw-root my_run/raw `
    --report-output my_run/report.json `
    --dry-run
```

---



## 6. Generate UA Spend dataset

UA Spend là dữ liệu chi phí acquisition (Facebook, Google Ads, TikTok…) để
tính CPI, ROAS, LTV:CAC. Tách biệt với game events.

### 6.1. Generate 30 ngày

```powershell
python simulation/generate_ua_spend.py --days 30 --output data/ua_spend/ua_spend.csv
```



### 6.2. Generate custom date range

```powershell
python simulation/generate_ua_spend.py `
    --days 60 `
    --start-date 2026-09-01 `
    --output data/ua_spend/ua_spend_60d.csv
```



### 6.3. Output schema (`ua_spend.csv`)

```csv
date,platform,country,source,campaign,spend,impressions,clicks,installs
2026-09-28,android,ID,facebook,US_AUG_01,2622.5,183705,4308,1082
2026-09-28,android,US,facebook,VN_AUG_01,2143.68,176450,6795,1091
...
```



### 6.4. Metrics từ UA spend

- **CPI** = spend / installs
- **CTR** = clicks / impressions
- **CVR** = installs / clicks
- **CAC** = spend / installs (= CPI)
- **ROAS** = revenue / spend (kết hợp với IAP events)
- **LTV:CAC** = ltv / cac (target ≥ 3.0)
- **Payback** = cac / arpu_per_month

---



## 7. Smoke tests



### 7.1. E2E smoke test (8 phases, 48 assertions, ~7s)

Chạy toàn bộ pipeline ở data thấp (20 players × 3 ngày) với so sánh
expected vs actual:

```powershell
cd prj
python simulation/smoke_test_e2e.py
```

**Kết quả mong đợi**:

```
======================================================================
  FINAL SUMMARY
======================================================================
  Total checks: 48
  Passed:       48
  Failed:       0

======================================================================
  SMOKE TEST PASSED – All phases work end-to-end
======================================================================
```

**Exit code**: `0` nếu PASS, `1` nếu FAIL (CI-friendly).

Smoke test chạy trong **isolated tmp dir** → không pollute `prj/data/`.

### 7.2. Per-module smoke tests

Mỗi module có smoke test riêng:

```powershell
python simulation/smoke_test_main.py            # Main pipeline components
python simulation/smoke_test_retention.py       # Retention curves
python simulation/smoke_test_ua_spend.py        # UA spend generator
python generators/smoke_test_player.py          # Player generator
python generators/smoke_test_progression.py     # Progression events
python generators/smoke_test_resource.py        # Resource events
python generators/smoke_test_business.py        # IAP events
python generators/smoke_test_ad.py              # Ad events
python generators/smoke_test_design.py          # Design events
python generators/smoke_test_error.py           # Error events
python generators/smoke_test_user_session.py    # User/session events
python gameanalytics/smoke_test_auth.py
python gameanalytics/smoke_test_client.py
python gameanalytics/smoke_test_compressor.py
python gameanalytics/smoke_test_validator.py
python gameanalytics/smoke_test_batcher.py
python gameanalytics/smoke_test_retry.py
python gameanalytics/smoke_test.py
python storage/smoke_test_queue.py
python storage/smoke_test_raw_writer.py
python storage/smoke_test.py
python config/smoke_test.py
python config/smoke_test_credentials.py
python models/smoke_test.py
```

Chạy tất cả:

```powershell
Get-ChildItem -Recurse -Filter smoke_test*.py | ForEach-Object { python $_.FullName }
```

---



## 8. Output: report, SQLite, raw files



### 8.1. Report JSON (`data/report.json`)

```json
{
  "start_date": "2026-09-29",
  "end_date": "2026-10-05",
  "n_days": 7,
  "total_players": 1000,
  "total_sessions": 5234,
  "total_events": 51234,
  "total_batches": 7,
  "total_revenue_minor": 142300,
  "total_revenue_usd": 1423.0,
  "total_paying_players": 47,
  "by_category": {
    "user": 5234, "progression": 10234, "design": 7123,
    "ad": 5012, "resource": 19234, "session_end": 5234
  },
  "by_country": { "VN": ..., "US": ..., ... },
  "by_day": [ /* DailyReport */ ],
  "wall_clock_sec": 12.34,
  "metrics": {
    "avg_dau": 747.7,
    "arpu_usd": 1.423,
    "arppu_usd": 30.27,
    "events_per_second": 4150,
    "avg_events_per_session": 9.79
  }
}
```



### 8.2. SQLite (`simulator.db`)

5 bảng chính:


| Table               | Mô tả                                                    |
| ------------------- | -------------------------------------------------------- |
| `players`           | Player state (level, coins, gems, churn_status…)         |
| `sessions`          | Mỗi session 1 row (user_id, start_time, transaction_num) |
| `progression_state` | World/level/attempt cho mỗi player                       |
| `player_economy`    | Currency balances (coins, gems, energy, lives…)          |
| `queue`             | Event queue (Bước 16)                                    |


Inspect nhanh:

```powershell
python -c "
import sqlite3
c = sqlite3.connect('simulator.db').cursor()
print('Players:', c.execute('SELECT COUNT(*) FROM players').fetchone()[0])
print('Sessions:', c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
print('Active users:', c.execute(\"SELECT COUNT(*) FROM players WHERE churn_status='active'\").fetchone()[0])
"
```



### 8.3. Raw data lake (`data/raw/`)

Partitioned theo Hive-style:

```
data/raw/
└── year=2026/
    └── month=09/
        └── day=29/
            ├── batch_000000.json.gz
            ├── batch_000001.json.gz
            └── ...
```

Mỗi file `.json.gz` chứa **JSON array** các GameAnalytics events:

```json
[
  {"v": 2, "user_id": "player_000001", "session_id": "...",
   "category": "user", "client_ts": 1790647330, ...},
  {"v": 2, "category": "progression", ...},
  ...
]
```

---



## 9. Các tham số CLI đầy đủ

```powershell
python main.py --help
```

```
Scale:
  --players INT       Số player cần generate (default 1000)
  --days INT          Số ngày simulation (default 7)
  --seed INT          Random seed (default 42)

Storage:
  --db PATH           SQLite DB path (default simulator.db)
  --raw-root PATH     Raw data lake root (default data/raw)

GameAnalytics:
  --ga-env {sandbox,production}    Môi trường GA (default sandbox)
  --env-file PATH                  Path tới .env (auto-detect nếu None)
  --dry-run                        Skip POST API (chỉ ghi raw + DB)

Reporting:
  --report-output PATH   Report JSON path (default data/report.json)

Misc:
  --start-date YYYY-MM-DD    Ngày bắt đầu (default today UTC)
  --log-level {DEBUG,INFO,WARNING,ERROR}    Log level (default INFO)
  --init-db                  Reset DB tables trước khi run
```

**Ví dụ tổng hợp**:

```powershell
python main.py `
    --players 50000 `
    --days 30 `
    --seed 42 `
    --ga-env sandbox `
    --db data/large_run.db `
    --raw-root data/large_run/raw `
    --report-output data/large_run/report.json `
    --start-date 2026-10-01 `
    --log-level INFO
```

---



## 10. Troubleshooting



### 10.1. `UnicodeEncodeError` trên Windows console

```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

Hoặc chạy qua `python -X utf8 main.py ...`.

### 10.2. `sqlite3.OperationalError: database is locked`

SimulationRunner mở/đóng connection mỗi session. Nếu gặp lock:

- Đảm bảo không có process khác đang truy cập `simulator.db` (DB Browser, sqlite3 CLI…)
- Restart simulation; lock sẽ tự giải phóng



### 10.3. Raw files quá lớn (>1 GB per run)

Bình thường với 100k+ players. Kiểm tra disk:

```powershell
Get-ChildItem data\raw -Recurse | Measure-Object -Property Length -Sum
```

Nén thêm (gzip level 9) hoặc partition theo campaign để dễ quản lý.

### 10.4. `Credentials not found` → tự dry-run

Đây là behavior mong đợi. Để gửi events thật, điền `.env` đúng.

### 10.5. Simulation chậm (>5 phút cho 1k players)

- Check `--log-level DEBUG` để xem bottleneck
- Mỗi session tốn ~10–20ms → 1k players × 7 ngày ≈ 70–140s là bình thường
- Nếu chậm hơn → check disk I/O (SSD khuyến nghị)



### 10.6. Smoke test FAIL với 1-2 checks

Chạy lại `smoke_test_e2e.py` để xác nhận deterministic. Nếu vẫn FAIL:

```powershell
python simulation/smoke_test_e2e.py 2>&1 | Tee-Object -FilePath smoke.log
# Xem "FAILED checks:" ở cuối
```

Các check thường gặp:

- `by_category counts match` / `by_country counts match` → sai seed hoặc simulation version
- `raw files created` → disk full hoặc permission denied



### 10.7. Reset toàn bộ state

```powershell
cd prj
Remove-Item simulator.db -ErrorAction SilentlyContinue
Remove-Item -Recurse data\raw -ErrorAction SilentlyContinue
python main.py --players 1000 --days 7 --init-db --dry-run
```

---



## Phụ lục: Quick reference


| Task               | Command                                                                                                                             |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| Run small dry-run  | `python main.py --players 1000 --days 7 --dry-run`                                                                                  |
| Run large dry-run  | `python main.py --players 100000 --days 30 --dry-run`                                                                               |
| Send to GA sandbox | `python main.py --players 5000 --days 14 --ga-env sandbox`                                                                          |
| Generate UA spend  | `python simulation/generate_ua_spend.py --days 30`                                                                                  |
| Run E2E smoke test | `python simulation/smoke_test_e2e.py`                                                                                               |
| Reset DB + run     | `python main.py --players 1000 --days 7 --init-db --dry-run`                                                                        |
| Inspect report     | Open `data/report.json`                                                                                                             |
| Inspect DB         | Use DB Browser for SQLite mở `simulator.db`                                                                                         |
| Inspect raw data   | `python -c "import gzip, json; print(json.loads(gzip.open('data/raw/year=2026/month=09/day=29/batch_000000.json.gz').read())[:3])"` |


---

**Tài liệu liên quan**:

- Spec chi tiết: `[README.md](./README.md)`
- UA Spend schema: `[prj/data/ua_spend/README.md](./prj/data/ua_spend/README.md)`
- Source code docs: docstrings trong từng module (`config/`, `generators/`, `simulation/`, `gameanalytics/`, `storage/`)

