# HƯỚNG DẪN TRIỂN KHAI MOBILE GAME EVENT SIMULATOR

> Tài liệu hướng dẫn triển khai `Mobile Game Event Simulator` theo đặc tả trong `README.md`.
> Mục tiêu: mô phỏng player behavior và gửi hàng triệu game events hợp lệ tới GameAnalytics Collection API (v2).

---

## 0. TỔNG QUAN DỰ ÁN

Simulator không phải là một chương trình tạo JSON ngẫu nhiên. Nó phải mô phỏng một **game stateful system** theo luồng:

```
Player
  ↓
Install
  ↓
Session → Gameplay → Progression (Ads / Economy / IAP)
  ↓
Retention / Churn → LTV / Monetization
```

Dataset sinh ra phải phục vụ được: DAU/WAU/MAU, Retention, Cohort, Progression, IAP Revenue, Ad Analytics, ARPU, ARPPU, LTV, Player Segmentation, Virtual Economy, Acquisition, ROAS.

---

## 1. ĐIỀU KIỆN CẦN CÓ TRƯỚC KHI BẮT ĐẦU

### 1.1. Công nghệ

- Python 3.10+
- `requests` (gọi HTTPS)
- `python-dotenv` (đọc `.env`)
- `sqlite3` (queue + player state)
- `gzip` (built-in)
- `hmac`, `hashlib`, `base64` (built-in cho auth)
- `uuid`, `json`, `time`, `random` (built-in)
- `pandas` (tuỳ chọn, cho UA spend CSV)

### 1.2. Tài khoản GameAnalytics

- Đăng ký game tại GameAnalytics → lấy `GAME_KEY` và `SECRET_KEY` từ **Game Settings**.
- Tạo file `.env`:

```env
GAME_KEY=your_game_key
SECRET_KEY=your_secret_key
GA_ENV=sandbox
```

- **KHÔNG** hard-code `SECRET_KEY` trong source code.
- Giai đoạn dev: dùng **Sandbox** (`https://sandbox-api.gameanalytics.com`). Chỉ chuyển sang Production khi integration hoàn chỉnh.

### 1.3. Kiến thức nền

- HTTPS / HMAC SHA-256 / Base64
- JSON list (không phải object) cho endpoint `/events`
- UUID v4 cho `session_id`

---

## 2. CÁC GIAI ĐOẠN MVP

Triển khai theo **4 phases** để tránh viết lại nhiều lần:


| Phase | Quy mô                       | Mục tiêu kiểm thử                                                                |
| ----- | ---------------------------- | -------------------------------------------------------------------------------- |
| 1     | 100 players × 1 day          | Luồng event cơ bản: `user`, `session_end`, `progression`, `business`, `resource` |
| 2     | 1,000 players × 7 days       | `retention`, `IAP`, `ads`, `economy`                                             |
| 3     | 10,000 players × 30 days     | `pipeline`, `batching`, `retry`, `raw storage`                                   |
| 4     | 100,000 players × 30–90 days | Full pipeline + 20–30M events                                                    |


**Quy mô thiết kế cuối cùng (Phase 4):**

- 100K players, 30–90 days
- 20–30M total events
- 2–3M sessions
- 100K–300K IAP purchases
- 5–10M ad impressions
- 5–10M progression events
- 5–10M resource events

---

## 3. CẤU TRÚC THƯ MỤC ĐỀ XUẤT

```
mobile-game-event-simulator/
├── config/
│   ├── game_config.py
│   ├── player_config.py
│   ├── event_config.py
│   └── monetization_config.py
├── models/
│   ├── player.py
│   ├── session.py
│   ├── progression.py
│   └── product.py
├── generators/
│   ├── player_generator.py
│   ├── session_generator.py
│   ├── progression_generator.py
│   ├── business_generator.py
│   ├── resource_generator.py
│   ├── ad_generator.py
│   ├── design_generator.py
│   └── error_generator.py
├── simulation/
│   ├── player_lifecycle.py
│   ├── session_lifecycle.py
│   ├── progression.py
│   ├── economy.py
│   ├── monetization.py
│   └── retention.py
├── gameanalytics/
│   ├── init.py
│   ├── annotations.py
│   ├── validator.py
│   ├── batcher.py
│   ├── compressor.py
│   ├── auth.py
│   └── client.py
├── storage/
│   ├── sqlite.py
│   ├── queue.py
│   └── raw_writer.py
├── data/
│   ├── raw/                    # year=YYYY/month=MM/day=DD/batch_XXX.json.gz
│   └── ua_spend/ua_spend.csv
├── .env
├── .gitignore
├── requirements.txt
└── main.py
```

---

## 4. THỨ TỰ TRIỂN KHAI CHI TIẾT (25 BƯỚC)

> Thứ tự này lấy trực tiếp từ section 57 của README, mở rộng chi tiết cho từng bước.

---

### BƯỚC 1 — Configuration

**Mục tiêu:** Tập trung mọi tham số simulation vào config để dễ điều chỉnh.

Tạo các file trong `config/`:

- `game_config.py`: `BUILD`, `SDK_VERSION = "rest api v2"`, `SAFE_LIMIT_BYTES` (ví dụ 900 KB < 1 MB), API base URL (sandbox/prod), endpoint paths.
- `player_config.py`: phân bổ player segments, acquisition sources, country list, platform/device pools.
- `event_config.py`: tỉ lệ error events (rất nhỏ, ví dụ 0.001), tỉ lệ ad theo engagement, default connection_type.
- `monetization_config.py`: product catalog (xem Bước 11), tỉ lệ IAP theo payer segment.

**Nguyên tắc:** Tất cả con số là **simulation assumption**, phải đặt trong config, không hard-code.

---

### BƯỚC 2 — Player Model

**Mục tiêu:** Định nghĩa cấu trúc dữ liệu cho một player.

Mỗi player có:

```text
user_id, country, platform, device, manufacturer, os_version,
install_date, acquisition_source, campaign_id,
player_segment (payer), engagement_segment, churn_segment,
current_level, coins, gems, energy,
session_count, transaction_num, lifetime_revenue, churn_status
```

Tạo `models/player.py` (dataclass hoặc Pydantic) với các field trên.

---

### BƯỚC 3 — Player Generator

**Mục tiêu:** Sinh tập players khởi đầu cho simulation.

Phân bổ theo **segment** (xem section 15 của README):

```
Payer:       Non-Payer / Low-Payer / Mid-Payer / Whale
Engagement:  Casual / Regular / Engaged / Hardcore
Lifecycle:   Early Churn / Normal / High Retention
```

Sinh `user_id` ổn định theo format `player_000001`, `player_000002`, … để trace xuyên suốt các session.

Cùng một player phải giữ nguyên `user_id` qua mọi session.

---

### BƯỚC 4 — Player State Storage

**Mục tiêu:** Lưu persistent state bằng SQLite.

Tạo `storage/sqlite.py` với các bảng:

- `players` (toàn bộ field của Player Model)
- `sessions`
- `progression_state`
- `player_economy`
- `queue` (event queue)

Các field bắt buộc **persistent**:

```
session_num, transaction_num, current_level,
coins, gems, last_session, churn_status
```

File DB: `simulator.db` (đặt trong project root, ignore trong `.gitignore`).

---

### BƯỚC 5 — Session Model

**Mục tiêu:** Định nghĩa cấu trúc một session.

Một session gồm:

```
session_start (user event)
  → gameplay
  → progression (Start/Fail/Complete)
  → ads (impression + rewarded)
  → resources (Source/Sink)
  → purchase (business event, optional)
  → session_end
```

Sinh `session_id` là UUID v4 (`550e8400-e29b-41d4-a716-446655440000`-style). **Mỗi session một UUID riêng**, không dùng chung cho cả player.

`session_num` bắt đầu từ `1` và tăng dần theo lifetime của installation.

**Session duration** không random đều `0 → 172800` mà dùng distribution theo engagement:


| Segment  | Duration       |
| -------- | -------------- |
| Casual   | 1–10 minutes   |
| Regular  | 10–30 minutes  |
| Engaged  | 20–60 minutes  |
| Hardcore | 30–120 minutes |


---

### BƯỚC 6 — Common Annotations (Default Fields)

**Mục tiêu:** Tạo factory trả về các field bắt buộc cho **mọi event**.

Tạo `gameanalytics/annotations.py` với hàm `create_default_annotations(player, session)` trả về:

```python
{
    "v": 2,
    "user_id": player.user_id,
    "client_ts": <unix_ts đã cộng offset server>,
    "sdk_version": "rest api v2",
    "os_version": player.os_version,        # "android 14", "ios 18.6", ...
    "manufacturer": player.manufacturer,    # samsung / apple / xiaomi / ...
    "device": player.device,                # "SM-S918B", "iPhone15,2", ...
    "platform": player.platform,            # "android" | "ios"
    "session_id": session.session_id,       # UUID
    "session_num": session.session_num,     # int >= 1
    "build": "1.0.0"                        # optional
}
```

Sau đó mỗi event generator chỉ cần thêm `category` + các field riêng.

> `sdk_version` **PHẢI là** `"rest api v2"`, không được tự đặt `"Python 1.0"` hay `"Simulator 1.0"`. Nếu muốn lưu version Simulator thì ghi riêng vào raw system metadata.

---

### BƯỚC 7 — User Event (Session Start)

**Mục tiêu:** Phát sinh event `user` — đại diện cho SESSION START.

**Rule:** User event phải là event đầu tiên trong batch đầu tiên của mỗi session và xuất hiện mỗi khi một session mới bắt đầu.

Tạo `generators/user_event_generator.py`. Payload mẫu:

```json
{
    "category": "user",
    "v": 2,
    "user_id": "player_000001",
    "client_ts": 1759305600,
    "sdk_version": "rest api v2",
    "os_version": "android 14",
    "manufacturer": "samsung",
    "device": "SM-S918B",
    "platform": "android",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "session_num": 1
}
```

---

### BƯỚC 8 — Session End Event

**Mục tiêu:** Phát sinh event `session_end` ở cuối mỗi session.

Required field: `length` (integer, `0 ≤ length ≤ 172800` giây).

**Rule bắt buộc:**

```
length = session_end.client_ts - session_start.client_ts
```

Tạo `generators/session_end_generator.py`. Payload mẫu:

```json
{
    "category": "session_end",
    "length": 842
}
```

---

### BƯỚC 9 — Progression Event

**Mục tiêu:** Mô phỏng Level/Stage/Chapter/Quest với 3 trạng thái `Start`, `Fail`, `Complete`.

Tạo `generators/progression_generator.py` + `simulation/progression.py`.

**Logic:**

- Dùng hierarchy cố định: `World → Level`. Ví dụ: `Start:World1:Level01`.
- **KHÔNG** đưa timestamp / UUID / user_id vào `event_id`.
- Theo dõi `attempt_num` trong player state:
  - Sau `Fail`: `attempt_num += 1`
  - Sau `Complete`: reset state
- Ví dụ flow:

```
Attempt 1 → Start → Fail
Attempt 2 → Start → Complete
```

Progression phải phụ thuộc engagement:

- Casual: ít level
- Regular: nhiều level
- Hardcore: rất nhiều level

---

### BƯỚC 10 — Resource Event (Virtual Economy)

**Mục tiêu:** Mô hình hoá Source / Sink cho các virtual currency.

Tạo `generators/resource_generator.py` + `simulation/economy.py`.

Resources: `Coins`, `Gems`, `Energy`, `Lives`, `Stars`, `Tickets`.

**Event ID format:**

```
[flowType]:[virtualCurrency]:[itemType]:[itemId]
```

Ví dụ:

- `Source:Coins:Gameplay:LevelReward` → amount `+500`
- `Sink:Coins:Shop:HealthPotion` → amount `-100`
- `Source:Gems:Purchase:GemPack`
- `Sink:Gems:Shop:PremiumItem`

**Quy tắc chống spam:**

- Nhận `+500 Coins` → chỉ tạo **một event** `+500`, KHÔNG tạo 500 event `+1`.

---

### BƯỚC 11 — Business Event (IAP)

**Mục tiêu:** Phát sinh event `business` cho real-money purchase.

Tạo `generators/business_generator.py` + `simulation/monetization.py`.

**Required fields:**

- `event_id`: `itemType:itemId` (ví dụ `GemPack:GemPack_500`)
- `amount`: integer theo **đơn vị nhỏ nhất** của currency (`$0.99 → 99`, `$4.99 → 499`)
- `currency`: 3-letter UPPERCASE (`USD`, `EUR`, `JPY`). KHÔNG dùng `usd`, `$`, `dollar`.
- `transaction_num`: counter tăng dần theo **lifetime** của player (KHÔNG reset mỗi session)

**Product catalog cố định** (lưu trong config):

```
GemPack_100, GemPack_500, GemPack_1200,
StarterPack, BattlePass, PremiumBundle, MegaBoost, SmallBoost
```

Mỗi product: `product_id`, `product_type`, `price` (minor unit), `currency`.

> **KHÔNG** tạo dynamic event ID như `GemPack:100:UUID:timestamp`.

**Logic payer:**

- Non-Payer → không có business event
- Low/Mid/Whale → số business event tăng theo segment
- `transaction_num` phải lưu persistent trong `player_economy`

---

### BƯỚC 12 — Ad / Impression Event

**Mục tiêu:** Mô phỏng Rewarded, Interstitial, Banner.

Tạo `generators/ad_generator.py`.

**Ad flow mẫu:**

```
Session
  ↓
Ad Impression
  ↓
Rewarded Ad
  ↓
Player receives reward
  ↓
Resource Source:Coins:Ad:Rewarded
```

**Quy tắc ad theo engagement:**

- Casual: ít ad
- Regular: vừa
- Hardcore: nhiều

Khi phát sinh Rewarded → tạo cặp (impression, resource source) để liên kết **Ad Engagement ↔ Economy ↔ Monetization**.

---

### BƯỚC 13 — Design Event

**Mục tiêu:** Gameplay actions tuỳ biến (kill, upgrade, open shop, complete quest...).

Tạo `generators/design_generator.py`.

Ví dụ `event_id`:

```
Gameplay:Kill:Zombie
Gameplay:Kill:Boss
Gameplay:Weapon:Upgrade
Gameplay:Shop:Open
Gameplay:Quest:Complete
```

Dùng field `value` (numeric) cho dynamic data. Ví dụ: `Gameplay:Kill:Boss` → `value: 738`.

**Quy tắc cực kỳ quan trọng:**

- **KHÔNG** đưa `user_id`, `timestamp`, `UUID`, `random number` vào event hierarchy.
- **KHÔNG** tạo quá nhiều unique event IDs (mục tiêu: giữ số node thấp để dashboard GameAnalytics dễ đọc).
- Dynamic info → dùng `value` hoặc custom event fields.

---

### BƯỚC 14 — Error Event

**Mục tiêu:** Mô phỏng debug / info / warning / error / critical.

Tạo `generators/error_generator.py`.

**Quy tắc:**

- `severity` enum đúng schema.
- `message` nằm trong schema cho phép.
- Xác suất rất nhỏ (`error_probability << 1`, ví dụ `0.001`).
- **KHÔNG** biến simulator thành "Error Generator". Giới hạn số error events trên mỗi game launch.

---

### BƯỚC 15 — Event Validator

**Mục tiêu:** Kiểm tra event trước khi đưa vào queue.

Tạo `gameanalytics/validator.py` với 3 lớp:

**Schema validation** (mọi event):

```
category, v, user_id, client_ts, sdk_version,
os_version, manufacturer, device, platform,
session_id, session_num
```

**Business validation:**

```
transaction_num tăng dần
attempt_num hợp lý
session_num tăng dần
purchase chỉ từ payer
Sink không làm balance âm (nếu rule game cấm)
```

**Batch validation:**

```
Mọi event cùng category schema đúng
```

---

### BƯỚC 16 — Event Queue

**Mục tiêu:** Cache event trước khi gửi, chịu được mất mạng.

Dùng SQLite (`storage/queue.py`) cho queue. Có thể dùng `JSONL` hoặc `Parquet` cho raw storage.

**Quy tắc:**

- Event fail POST → **KHÔNG** discard, đưa vào Retry Queue.
- Khi network restored → tiếp tục gửi.
- Đây là pattern GameAnalytics khuyến nghị: client cache + submit định kỳ.

---

### BƯỚC 17 — Batcher (1 MB limit)

**Mục tiêu:** Gom event thành batch có compressed size < 1 MB.

Tạo `gameanalytics/batcher.py`.

**Quy tắc:**

- GameAnalytics có **POST size limit = 1 MB**.
- KHÔNG dùng `batch_size = 10000` cố định vì event size không đều.
- Kiểm tra `compressed_payload_size` sau gzip.
- Đặt `SAFE_LIMIT` thấp hơn 1 MB (ví dụ 900 KB) để có margin.

Pipeline:

```
Events
  ↓ add event
Serialize JSON
  ↓
Check size
  ↓
< SAFE_LIMIT?  → YES → add next
                → NO  → flush current batch
```

> **Không được** load 20–30M events vào RAM. Phải Generate → Batch → Write → Send → Clear → lặp tiếp.

---

### BƯỚC 18 — GZIP Compression

**Mục tiêu:** Nén payload trước khi gửi.

Tạo `gameanalytics/compressor.py`.

Pipeline đúng:

```
events
  ↓
JSON serialization
  ↓
gzip compression
  ↓
HMAC trên compressed body   ← quan trọng: HMAC trên GZIP DATA, không phải JSON
  ↓
HTTP POST
```

Header:

```
Content-Type: application/json
Content-Encoding: gzip
```

---

### BƯỚC 19 — HMAC Authentication

**Mục tiêu:** Sinh `Authorization` header đúng chuẩn GameAnalytics.

Tạo `gameanalytics/auth.py`.

Công thức:

```
HMAC_SHA256(request_body, SECRET_KEY)  →  Base64  →  Authorization
```

**Hai trường hợp:**


| Mode       | Body dùng để tính HMAC |
| ---------- | ---------------------- |
| Không gzip | Raw JSON               |
| Có gzip    | Gzipped bytes          |


> **Không được** tính HMAC trên JSON rồi mới gzip nếu request body thực tế là gzipped data — server sẽ reject.

Header cuối:

```
Authorization: <base64_hmac>
Content-Type: application/json
Content-Encoding: gzip   (nếu dùng gzip)
```

---

### BƯỚC 20 — GameAnalytics Client

**Mục tiêu:** Gọi API và xử lý response.

Tạo `gameanalytics/client.py` với 2 method:

**1.** `POST /v2/<game_key>/init` — gọi khi bắc đầu session / launch:

```
Session Start
  ↓
Init
  ↓
server_ts
  ↓
Calculate clock offset = server_ts - local_timestamp
  ↓
client_ts = local_event_timestamp + offset   (khi tạo event)
```

Response có thể chứa `configs_hash` → lưu lại nếu triển khai Remote Config/A-B Testing. Ở phase đầu có thể bỏ qua.

**2.** `POST /v2/<game_key>/events` — gửi batch:

```
Body = JSON list (KHÔNG phải object), ví dụ:
[
    { "category": "user", ... },
    { "category": "progression", ... }
]
```

Response mong đợi: `HTTP 200 OK`.

Log mỗi request:

```
batch_id
event_count
payload_size
status_code
timestamp
retry_count
```

---

### BƯỚC 21 — Retry Mechanism

**Mục tiêu:** Chịu lỗi mạng / 5xx từ GameAnalytics.

Tạo module retry (có thể nằm trong `client.py` hoặc tách riêng).

Quy tắc:

```
Batch → POST
  ├── 200 OK → Mark sent
  └── FAIL → Retry Queue → Retry (exponential backoff, giới hạn max retry)
```

> **Không xoá raw batch trước khi request thành công.** Đây là rule data quality quan trọng.

---

### BƯỚC 22 — Raw Data Writer

**Mục tiêu:** Lưu raw data **độc lập** với GameAnalytics để có thể replay.

Tạo `storage/raw_writer.py`.

Cấu trúc thư mục:

```
data/
└── raw/
    └── year=YYYY/
        └── month=MM/
            └── day=DD/
                ├── batch_000001.json.gz
                ├── batch_000002.json.gz
                └── ...
```

Mục đích:

```
Simulator
  ├──→ GameAnalytics Collection API
  └──→ Local Raw Data Lake

API gặp lỗi → Raw Data → Replay → Pipeline
```

---

### BƯỚC 23 — Retention / Churn

**Mục tiêu:** Mô hình hoá lifecycle đầy đủ của player.

Tạo `simulation/retention.py`.

Lifecycle:

```
ACQUISITION
  ↓
INSTALL
  ↓
FIRST SESSION
  ↓
ACTIVATION
  ↓
ENGAGEMENT
  ↓
MONETIZATION
  ↓
RETENTION
  ↓
CHURN
```

Mỗi player có `install_date`. Tính `player_age = simulation_date - install_date`.

**Cohort & retention checkpoints:**

```
D1, D3, D7, D14, D30
```

Mỗi cohort có retention curve khác nhau (Early Churn / Normal / High Retention).

**Quy tắc:** KHÔNG để tất cả player active mỗi ngày — phải có cả churn và dormant.

---

### BƯỚC 24 — UA Spend Dataset

**Mục tiêu:** Bổ sung dữ liệu chi tiêu acquisition để tính ROAS.

Tạo `data/ua_spend/ua_spend.csv` với schema:

```csv
date,platform,country,source,campaign,spend,impressions,clicks,installs
2026-09-01,android,VN,facebook,VN_A,500,120000,4200,850
```

> **UA Spend KHÔNG phải player purchase event** — đây là nguồn riêng.

Sau khi có cả Game Events + UA Spend, có thể tính:

```
CPI = spend / installs
ROAS = revenue / spend
LTV:CAC
Payback
```

Acquisition sources mô phỏng:

```
organic, facebook, google_ads, tiktok
```

Campaigns mô phỏng:

```
VN_AUG_01, VN_AUG_02, US_AUG_01, JP_AUG_01
```

---

### BƯỚC 25 — Large-Scale Simulation

**Mục tiêu:** Chạy full simulation 100K players × 30–90 ngày.

Tạo `main.py` với flow:

```text
START
  ↓
Load configuration
  ↓
Load GameAnalytics credentials (.env)
  ↓
Initialize database (SQLite)
  ↓
Create / load players
  ↓
For each simulation day:
  │
  ├── Determine active players (retention model)
  │
  ├── Create sessions
  │
  ├── Create user event (session start)
  │
  ├── Generate gameplay (progression + design)
  │
  ├── Generate ads (impression + rewarded)
  │
  ├── Generate resources (source/sink)
  │
  ├── Generate IAP (business, theo payer segment)
  │
  ├── Generate rare errors
  │
  ├── Generate session_end
  │
  ├── Validate
  │
  ├── Write raw
  │
  ├── Build batches (< 1 MB gzip)
  │
  └── POST GameAnalytics
  ↓
Next simulation day
  ↓
FINISH
```

Nhớ:

- Dùng **stream/batch** chứ không load toàn bộ events vào RAM.
- Mỗi batch xong → ghi raw + gửi API → clear buffer → lặp tiếp.

---

## 5. CHECKLIST TRƯỚC KHI GỬI EVENT (GAMEANALYTICS API)

- [ ] HTTPS (không HTTP)
- [ ] Đúng GAME_KEY
- [ ] Đúng SECRET_KEY (load từ .env, không hard-code)
- [ ] Đúng môi trường (sandbox / production)
- [ ] POST /v2//events
- [ ] Content-Type: application/json
- [ ] Body là JSON LIST (kể cả chỉ 1 event)
- [ ] category hợp lệ
- [ ] v = 2
- [ ] user_id hợp lệ, ổn định theo player
- [ ] client_ts hợp lệ (theo thứ tự logic)
- [ ] sdk_version = "rest api v2"
- [ ] os_version khớp platform
- [ ] manufacturer, device, platform hợp lệ
- [ ] session_id là UUID v4
- [ ] session_num >= 1, tăng dần
- [ ] HMAC SHA-256 trên đúng body (raw JSON hoặc gzip bytes)
- [ ] Base64 Authorization
- [ ] Payload gzip < SAFE_LIMIT (< 1 MB)
- [ ] Retry mechanism đang bật
- [ ] Queue mechanism đang bật
- [ ] Có raw backup trước khi POST

---

## 6. CHECKLIST CHẤT LƯỢNG DỮ LIỆU (SIMULATOR)

- [ ] Player Generator chạy đúng segment distribution
- [ ] Player State persistent (SQLite)
- [ ] Player có lifecycle đầy đủ
- [ ] Install date hợp lý
- [ ] Acquisition source + campaign đa dạng
- [ ] Platform / country / device phân bố
- [ ] Session generator có user + session_end
- [ ] Session duration theo distribution theo engagement
- [ ] Progression có Start / Fail / Complete
- [ ] attempt_num tăng đúng
- [ ] IAP chỉ từ payer
- [ ] transaction_num tăng dần, persistent
- [ ] Product catalog cố định
- [ ] Ad + Impression sinh đúng flow
- [ ] Resource Source / Sin k theo economy
- [ ] Design event hợp lý, không quá nhiều unique IDs
- [ ] Error event rất hiếm
- [ ] Custom fields hữu ích (game_mode, ab_group, ...)
- [ ] Retention + Churn tồn tại
- [ ] Cohort phân biệt
- [ ] UA Spend CSV tồn tại
- [ ] Raw storage đúng partition year/month/day
- [ ] Queue chịu được retry
- [ ] Validator bắt được schema + business lỗi
- [ ] Batcher tuân thủ < 1 MB
- [ ] Compression gzip
- [ ] HMAC đúng chuẩn
- [ ] API client có retry + logging

---

## 7. CHECKLIST CHẤT LƯỢNG MÔ PHỎNG (DATA REALISM)

- [ ] KHÔNG random event độc lập
- [ ] Player có state
- [ ] Player có behavioral segment
- [ ] Session liên quan player
- [ ] Progression liên quan session
- [ ] Purchase liên quan payer
- [ ] Ad liên quan session + engagement
- [ ] Resource liên quan gameplay
- [ ] Churn tồn tại và rõ ràng
- [ ] Retention curve đa dạng
- [ ] Cohort phân biệt theo install_date
- [ ] Có acquisition source
- [ ] Số unique event IDs ở mức thấp
- [ ] Không spam resource events
- [ ] Không spam error events
- [ ] Có raw data để replay
- [ ] Có thể replay pipeline từ raw

---

## 8. NGUYÊN TẮC THIẾT KẾ CỐT LÕI

**Phải xây theo luồng:**

```
GAME STATE
  ↓
PLAYER BEHAVIOR
  ↓
EVENT
  ↓
QUEUE
  ↓
VALIDATION
  ↓
BATCH
  ↓
GZIP
  ↓
HMAC
  ↓
GAMEANALYTICS
```

**KHÔNG được xây theo:**

```
random() → random() → random() → JSON → API
```

Player state quyết định tất cả event:

```
Hardcore + Whale  →  High frequency sessions, high progression, high ad, high IAP
Casual + Non-Payer →  Few sessions, low progression, few ads, no IAP, có thể churn sớm
```

---

## 9. METRICS MÀ DATASET PHẢI HỖ TRỢ


| Nhóm        | Metrics                                                                     |
| ----------- | --------------------------------------------------------------------------- |
| Acquisition | Installs, Clicks, Impressions, CTR, CPI                                     |
| Engagement  | DAU, WAU, MAU, Sessions, Sessions/User, Session Duration                    |
| Retention   | D1, D3, D7, D14, D30                                                        |
| Progression | Level Starts, Completes, Fails, Completion Rate, Failure Rate, Avg Attempts |
| IAP         | Revenue, Transactions, Paying Users, ARPU, ARPPU, IAP Conversion            |
| Ads         | Impressions, Ad Revenue, eCPM, Ad ARPDAU                                    |
| Economy     | Coins/Gems Source/Sink, Net Flow                                            |
| LTV         | D0, D1, D7, D30 LTV                                                         |
| UA          | Spend, Installs, CPI, Revenue, ROAS, LTV:CAC, Payback                       |


---

## 10. DATA MODEL MỤC TIÊU (SAU TRANSFORM)

**Raw:** `events`, `players`, `ua_spend`

**Core (fact/dim):**

```
dim_player, dim_date
fact_session, fact_progression, fact_purchase,
fact_ad_impression, fact_resource, fact_design, fact_error
```

**Marts:**

```
mart_daily_engagement
mart_daily_monetization
mart_retention
mart_cohort
mart_ltv
mart_ua_performance
mart_player_economy
```

---

## 11. PHÂN BIỆT RÕ: API RULE vs SIMULATOR RULE

**GameAnalytics API quyết định (technical contract — không thay đổi được):**

```
HTTPS, JSON, POST, Game Key, Secret Key, HMAC, API v2,
event categories, event schema, default annotations,
1 MB POST limit, HTTP response, queue recommendation, Init
```

**Simulator quyết định (business/behavior — cấu hình được):**

```
Số player, số ngày, số event,
player segments, engagement distribution,
payer distribution, retention distribution,
session distribution, progression model, IAP model,
ad model, economy model, acquisition model,
UA spend, churn model
```

Đây là hai lớp tách biệt — API contract là bất biến, simulation assumption là biến.

---

## 12. CHECKLIST CUỐI CÙNG

Khi đã làm đủ 25 bước + pass 3 checklist ở mục 5, 6, 7:

- [ ] Phase 1 (100 players × 1 day) chạy thành công
- [ ] Phase 2 (1K players × 7 days) qua được test retention/IAP/ads/economy
- [ ] Phase 3 (10K players × 30 days) qua được test pipeline/batching/retry/raw
- [ ] Phase 4 (100K players × 30–90 days, 20–30M events) chạy ổn định
- [ ] Raw data có thể replay toàn bộ pipeline
- [ ] Dataset support đủ 9 nhóm metrics ở mục 9
- [ ] Data warehouse + Power BI dashboard đọc được từ raw data

→ Simulator hoàn thành đúng tinh thần **"Mobile Game Event Simulator không phải là random data generator, mà là một game stateful system mô phỏng"**.