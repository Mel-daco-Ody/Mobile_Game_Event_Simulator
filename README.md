# Mobile Game Event Simulator

## Specification & Rules for GameAnalytics Collection API

> **Purpose:** Tài liệu đặc tả để tự xây dựng một `Mobile Game Event Simulator` bằng Python, có khả năng mô phỏng player behavior và gửi hàng triệu game events hợp lệ tới GameAnalytics Collection API, đồng thời lưu raw data để phục vụ Data Engineering / Data Analytics.

---

# 0. MỤC TIÊU CỦA PROJECT

Simulator không chỉ là một chương trình tạo random JSON.

Mục tiêu là xây dựng một hệ thống mô phỏng:

```text
Player
  ↓
Install
  ↓
Session
  ↓
Gameplay
  ↓
Progression
  ├── Ads
  ├── Resource / Economy
  └── IAP
  ↓
Retention / Churn
  ↓
LTV / Monetization

```

Simulator phải tạo được dữ liệu đủ thực tế để phục vụ:

- DAU / WAU / MAU
- Session Analytics
- Retention
- Cohort Analysis
- Progression Analytics
- IAP Revenue
- Ad Analytics
- ARPU
- ARPPU
- IAP Conversion
- LTV
- Player Segmentation
- Virtual Economy
- Acquisition Analytics
- ROAS nếu kết hợp với UA Spend

---



# 1. KIẾN TRÚC TỔNG THỂ

Kiến trúc đề xuất:

```text
┌──────────────────────────────────────┐
│       MOBILE GAME EVENT SIMULATOR    │
│                                      │
│  Player Generator                    │
│  Player State                         │
│  Session Generator                    │
│  Progression Generator                │
│  IAP Generator                        │
│  Ad Generator                         │
│  Resource Generator                   │
│  Design Generator                     │
└───────────────────┬──────────────────┘
                    │
                    ▼
             Event Queue
                    │
                    ▼
             Event Validator
                    │
                    ▼
              Raw Data Writer
                    │
          ┌─────────┴─────────┐
          │                   │
          ▼                   ▼
    Local Raw Data      GameAnalytics
                              │
                              ▼
                   GameAnalytics Export
                              │
                              ▼
                           Data Lake
                              │
                              ▼
                           Airflow
                              │
                              ▼
                             dbt
                              │
                              ▼
                       Data Warehouse
                              │
                              ▼
                          Power BI

```

---



# 2. PHẦN I — RULES CỦA GAMEANALYTICS COLLECTION API



## 2.1. GameAnalytics Collection API

GameAnalytics Collector nhận game events thông qua HTTPS.

Collection API hiện tại sử dụng API version:

```text
v2

```

Hai route chính:

```text
POST /v2/<game_key>/init
POST /v2/<game_key>/events

```

GameAnalytics sử dụng chính API này cho các SDK chính thức.

---



# 2.2. Game Key và Secret Key

Simulator cần:

```text
GAME_KEY
SECRET_KEY

```



### GAME_KEY

Identifier của game.

### SECRET_KEY

Secret dùng để tạo HMAC authentication cho request.

Credentials phải lấy từ GameAnalytics Game Settings.

Không hard-code Secret Key trong source code.

Nên sử dụng:

```text
.env

```

Ví dụ:

```env
GAME_KEY=your_game_key
SECRET_KEY=your_secret_key
GA_ENV=sandbox

```

---



# 2.3. Sandbox và Production

Trong giai đoạn phát triển phải sử dụng Sandbox.

### Sandbox

```text
https://sandbox-api.gameanalytics.com

```



### Production

```text
https://api.gameanalytics.com

```

Event endpoint:

```text
https://sandbox-api.gameanalytics.com/v2/<GAME_KEY>/events

```

Production:

```text
https://api.gameanalytics.com/v2/<GAME_KEY>/events

```



### Rule

Trong quá trình phát triển:

```text
Simulator
   ↓
Sandbox

```

Sau khi integration hoàn chỉnh mới:

```text
Simulator
   ↓
Production

```

Không sử dụng production key trên sandbox.

---



# 2.4. HTTPS

Chỉ sử dụng:

```text
HTTPS

```

Không sử dụng HTTP.

---



# 2.5. JSON

Request body phải là JSON.

Đối với `/events`, body phải là:

```json
[
    {
        "category": "user"
    },
    {
        "category": "progression"
    }
]

```

Quan trọng:

> Events endpoint luôn nhận một JSON LIST, kể cả khi chỉ gửi một event.

Ví dụ đúng:

```json
[
    {
        "category": "user"
    }
]

```

Không phải:

```json
{
    "category": "user"
}

```

---



# 2.6. HTTP Method

Events:

```text
POST /v2/<game_key>/events

```

Init:

```text
POST /v2/<game_key>/init

```

---



# 2.7. Headers

Header bắt buộc:

```http
Authorization: <HMAC_HASH>
Content-Type: application/json

```

Nếu gzip:

```http
Authorization: <HMAC_HASH>
Content-Type: application/json
Content-Encoding: gzip

```

`Content-Length` có thể được gửi nếu có thể xác định.

---



# 2.8. HMAC Authentication

Authorization sử dụng:

```text
HMAC SHA-256

```

Công thức:

```text
HMAC_SHA256(
    request_body,
    SECRET_KEY
)

```

Sau đó:

```text
Base64 Encode

```

Kết quả được gửi trong:

```http
Authorization: <base64_hmac>

```



### Nếu không gzip

Tính HMAC trên raw request body.

```text
JSON
 ↓
HMAC-SHA256
 ↓
Base64
 ↓
Authorization

```



### Nếu gzip

Phải:

```text
JSON
 ↓
GZIP
 ↓
HMAC-SHA256 trên GZIP DATA
 ↓
Base64
 ↓
Authorization

```

Đây là điểm rất quan trọng.

Không được:

```text
JSON
 ↓
HMAC
 ↓
GZIP

```

nếu request body thực tế là gzipped data.

---



# 2.9. Gzip

GameAnalytics hỗ trợ gzip và khuyến nghị mạnh sử dụng gzip.

Simulator nên sử dụng:

```text
Content-Encoding: gzip

```

Pipeline:

```text
events
 ↓
JSON serialization
 ↓
gzip compression
 ↓
HMAC trên compressed body
 ↓
HTTP POST

```

---



# 2.10. POST Payload Limit

GameAnalytics Collector có:

```text
1 MB POST size limit

```

Do đó Simulator không được gửi một request chứa quá nhiều dữ liệu.

Không:

```text
20,000,000 events
        ↓
       POST

```

Mà:

```text
20,000,000 events
        ↓
     Batching
        ↓
Batch 001
Batch 002
Batch 003
...

```

Batch phải được kiểm soát theo kích thước payload.

Không nên chỉ dựa vào:

```python
batch_size = 10000

```

vì kích thước mỗi event không giống nhau.

Nên kiểm tra:

```text
compressed_payload_size

```

và giữ buffer dưới giới hạn 1 MB.

Nên đặt một `SAFE_LIMIT` thấp hơn 1 MB để có margin an toàn.

---



# 2.11. HTTP 200

Nếu Events request trả:

```text
HTTP 200 OK

```

thì request thành công và các events trong request được collector thu nhận.

Simulator phải log:

```text
batch_id
event_count
payload_size
status_code
timestamp
retry_count

```

Ví dụ:

```text
Batch 000001
Events: 812
Payload: 734 KB
Status: 200
Result: SUCCESS

```

---



# 2.12. Event Queue

GameAnalytics khuyến nghị client cache events và submit định kỳ.

Có thể dùng:

```text
SQLite

```

để lưu queue.

Kiến trúc:

```text
Event Generator
      ↓
Event Queue
      ↓
Batch Builder
      ↓
API Sender

```

Khi mất kết nối:

```text
Event
 ↓
Queue
 ↓
Network restored
 ↓
Send

```

Không được tự động discard event chỉ vì request thất bại.

---



# 2.13. Retry

Simulator phải có retry mechanism.

Ví dụ:

```text
Batch
 ↓
POST
 ↓
Success → DONE

```

Nếu thất bại:

```text
Batch
 ↓
POST
 ↓
FAILED
 ↓
Retry Queue
 ↓
Retry

```

Nên giới hạn số lần retry và sử dụng exponential backoff.

---



# 2.14. Init API

Route:

```text
POST /v2/<game_key>/init

```

Init nên được gọi khi bắt đầu session / launch.

Init có thể cung cấp:

- `server_ts`
- remote configuration
- A/B testing information
- timestamp adjustment

Simulator có thể triển khai:

```text
Session Start
      ↓
Init
      ↓
server_ts
      ↓
Calculate clock offset
      ↓
Generate events

```

---



# 2.15. Server Timestamp

GameAnalytics trả:

```text
server_ts

```

Simulator có thể tính:

```text
offset = server_ts - local_timestamp

```

Khi tạo event:

```text
client_ts =
local_event_timestamp + offset

```

Mục tiêu là tránh timestamp của simulator bị lệch quá xa so với server.

---



# 2.16. Configs Hash

Init response có thể chứa:

```text
configs_hash

```

Simulator nếu triển khai Remote Config nên lưu giá trị này và sử dụng lại trong những lần gọi init tiếp theo.

Nếu chỉ cần Event Collection cơ bản, có thể để Remote Config/A-B Testing ở phase sau.

---



# 3. DEFAULT ANNOTATIONS — COMMON EVENT FIELDS

Mỗi event có các default annotations.

Các field required quan trọng:

```text
device
v
user_id
client_ts
sdk_version
os_version
manufacturer
platform
session_id
session_num

```

Một số field khác là optional.

---



# 3.1. `v`

Required:

```json
"v": 2

```

Current event version:

```text
2

```

---



# 3.2. `user_id`

Required.

Đây là ID nhận diện user/device.

Rule của Simulator:

```text
Một player
    ↓
Một user_id ổn định

```

Ví dụ:

```text
player_000001
player_000002
player_000003

```

Cùng player phải giữ nguyên `user_id` qua các session.

---



# 3.3. `client_ts`

Timestamp event được tạo/đưa vào queue.

Kiểu:

```text
integer

```

Thông thường là Unix epoch.

Simulator phải đảm bảo timestamp có thứ tự logic.

Ví dụ:

```text
session_start
      <
progression
      <
ad
      <
purchase
      <
session_end

```

---



# 3.4. `sdk_version`

Với custom solution sử dụng Collection API:

```json
"sdk_version": "rest api v2"

```

Không tự đặt:

```text
Python 1.0
Simulator 1.0

```

cho field này.

Có thể lưu version của Simulator bằng một field riêng trong raw system metadata nếu cần.

---



# 3.5. `os_version`

Ví dụ:

```text
android 14
android 15
ios 18.6
ios 19

```

Phải phù hợp với platform.

---



# 3.6. `manufacturer`

Ví dụ:

```text
samsung
apple
xiaomi
oppo
vivo
google

```

---



# 3.7. `device`

Ví dụ:

```text
SM-S918B
SM-A556E
iPhone15,2
iPhone17,1

```

Giữ độ dài hợp lý.

---



# 3.8. `platform`

GameAnalytics có danh sách platform hợp lệ.

Đối với Mobile Game Simulator:

```text
android
ios

```

là đủ.

Nếu muốn mô phỏng server-side events có thể dùng:

```text
server

```

nhưng không nên dùng tùy tiện.

---



# 3.9. `session_id`

Required.

Phải có format UUID:

```text
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

```

Ví dụ:

```text
550e8400-e29b-41d4-a716-446655440000

```

Mỗi session phải có một `session_id` riêng.

```text
Player A
 ├── Session 1 → UUID A
 ├── Session 2 → UUID B
 └── Session 3 → UUID C

```

Không dùng một UUID cho toàn bộ player.

---



# 3.10. `session_num`

Required.

Bắt đầu:

```text
1

```

và tăng theo lifetime của player/app installation:

```text
Session 1 → 1
Session 2 → 2
Session 3 → 3
...

```

Nên lưu persistent state.

---



# 3.11. `build`

Optional.

Có thể dùng:

```text
1.0.0
1.1.0
1.2.0

```

Simulator nên giữ một số build cố định hoặc mô phỏng release theo thời gian.

---



# 3.12. `connection_type`

Optional.

Giá trị hợp lệ:

```text
offline
wwan
wifi
lan

```

Mobile simulator nên ưu tiên:

```text
wifi
wwan

```

---



# 4. EVENT CATEGORIES

GameAnalytics Collection API hỗ trợ:

```text
user
session_end
business
progression
resource
design
error
ad events
impression events

```

---



# 5. USER EVENT

Category:

```json
"category": "user"

```

User event đại diện cho:

```text
SESSION START

```

Rule:

> User event phải là event đầu tiên trong batch đầu tiên của session và được thêm mỗi khi một session mới bắt đầu.

Simulator:

```text
new session
    ↓
create user event
    ↓
create gameplay events

```

Ví dụ cấu trúc:

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



# 6. SESSION_END EVENT

Category:

```json
"category": "session_end"

```

Required:

```text
length

```

`length`:

```text
integer
0 ≤ length ≤ 172800 seconds

```

Ví dụ:

```json
{
    "category": "session_end",
    "length": 842
}

```

Simulator phải đảm bảo:

```text
length =
session_end.client_ts
-
session_start.client_ts

```

trong mô hình simulation.

---



# 7. BUSINESS EVENT — IAP

Business event dùng cho:

```text
REAL-MONEY PURCHASE

```

Không dùng cho virtual currency.

Required:

```text
category
event_id
amount
currency
transaction_num

```

---



# 7.1. `event_id`

Format:

```text
itemType:itemId

```

Ví dụ:

```text
GemPack:GemPack_100
GemPack:GemPack_500
Boost:MegaBoost
BattlePass:Season_01

```

Nên có product catalog cố định.

Không tạo dynamic event IDs.

Không:

```text
GemPack:100:UUID:timestamp

```

---



# 7.2. `amount`

Amount là integer theo đơn vị nhỏ nhất của currency.

Ví dụ USD:

```text
$0.99 → 99
$4.99 → 499
$9.99 → 999
$19.99 → 1999

```

Simulator nên lưu:

```text
price_minor

```

---



# 7.3. `currency`

Phải:

```text
3-letter
UPPERCASE

```

Ví dụ:

```text
USD
EUR
JPY

```

Không:

```text
usd
$
dollar

```

---



# 7.4. `transaction_num`

Counter tăng dần theo lifetime của installation/player.

Ví dụ:

```text
Purchase 1 → 1
Purchase 2 → 2
Purchase 3 → 3

```

Không reset về 1 mỗi session.

Simulator phải lưu:

```text
player.transaction_num

```

---



# 7.5. Optional Business Fields

Có thể sử dụng:

```text
cart_type
receipt_info

```

Ví dụ:

```text
cart_type:
menu_shop
end_of_level_shop

```

Trong Simulator không cần mô phỏng receipt validation ở phase đầu.

---



# 8. PROGRESSION EVENT

Dùng để mô phỏng:

```text
Level
Stage
Chapter
World
Quest

```

Trạng thái chính:

```text
Start
Fail
Complete

```

Ví dụ:

```text
Start:World1:Level01
Fail:World1:Level01
Start:World1:Level01
Complete:World1:Level01

```

---



# 8.1. Progression Logic

Không random độc lập.

Ví dụ:

```text
Level 5

Attempt 1
    Start
    Fail

Attempt 2
    Start
    Complete

```

State:

```text
attempt_num = 1

```

sau Fail:

```text
attempt_num = 2

```

sau Complete:

```text
reset progression attempt state

```

---



# 8.2. Progression Hierarchy

Nên sử dụng:

```text
World
Level

```

Ví dụ:

```text
Start:World1:Level01
Complete:World1:Level01

Start:World1:Level02
Complete:World1:Level02

```

Không tạo hierarchy có timestamp hoặc UUID.

---



# 9. RESOURCE EVENT

Resource dùng cho virtual economy.

Ví dụ:

```text
Coins
Gems
Energy
Lives
Stars
Tickets

```

Resource event mô hình hóa:

```text
SOURCE

```

và:

```text
SINK

```

---



# 9.1. Source

Player nhận resource.

Ví dụ:

```text
Source:Coins:Gameplay:LevelReward

```

Amount:

```text
positive

```

Ví dụ:

```text
+500 Coins

```

---



# 9.2. Sink

Player tiêu resource.

Ví dụ:

```text
Sink:Coins:Shop:HealthPotion

```

Amount:

```text
negative

```

Ví dụ:

```text
-100 Coins

```

---



# 9.3. Resource Event ID

Có thể tổ chức:

```text
[flowType]:[virtualCurrency]:[itemType]:[itemId]

```

Ví dụ:

```text
Source:Coins:Gameplay:LevelReward
Sink:Coins:Shop:HealthPotion
Source:Gems:Purchase:GemPack
Sink:Gems:Shop:PremiumItem

```

---



# 9.4. Không spam Resource Events

Nếu player nhận:

```text
500 Coins

```

không tạo:

```text
+1
+1
+1
...

```

Nên tạo:

```text
+500

```

một lần.

---



# 10. DESIGN EVENT

Design events dùng cho gameplay actions tùy chỉnh.

Ví dụ:

```text
Gameplay:Kill:Zombie
Gameplay:Kill:Boss
Gameplay:Weapon:Upgrade
Gameplay:Shop:Open
Gameplay:Quest:Complete

```

Có thể sử dụng `value` cho numeric value.

Ví dụ:

```text
event_id:
Gameplay:Kill:Boss

value:
738

```

---



# 10.1. Không tạo quá nhiều unique event IDs

Không:

```text
Gameplay:Kill:Zombie:player001
Gameplay:Kill:Zombie:player002
Gameplay:Kill:Zombie:player003

```

Không đưa:

```text
timestamp
UUID
user_id
random number

```

vào event hierarchy.

Nên:

```text
Gameplay:Kill:Zombie

```

và lưu numeric/dynamic information bằng:

```text
value

```

hoặc Custom Event Fields.

Mục tiêu là giữ số unique event nodes ở mức thấp.

---



# 11. ERROR EVENT

Dùng để mô phỏng:

```text
debug
info
warning
error
critical

```

Message phải nằm trong giới hạn schema của GameAnalytics.

Simulator nên tạo error với xác suất thấp.

Không được biến simulator thành:

```text
Error Generator

```

GameAnalytics cảnh báo không nên gửi quá nhiều error events; implementation nên giới hạn số error events trên mỗi game launch.

Đề xuất:

```text
error_probability << 1

```

---



# 12. AD EVENTS VÀ IMPRESSION EVENTS

Simulator nên mô phỏng:

```text
Rewarded
Interstitial
Banner

```

Ví dụ business flow:

```text
Session
  ↓
Ad Impression
  ↓
Rewarded Ad
  ↓
Player receives reward
  ↓
Resource Source

```

Ví dụ:

```text
Ad Impression
    ↓
Rewarded
    ↓
Source:Coins:Ad:Rewarded

```

Điều này giúp liên kết:

```text
Ad Engagement
      ↓
Economy
      ↓
Monetization

```

---



# 13. CUSTOM EVENT FIELDS

Custom Event Fields dùng để bổ sung dữ liệu không phù hợp với standard event schema.

Ví dụ:

```text
score
xp_gained
currency_earned
item_name
difficulty
game_mode
ab_group

```

Custom fields được gửi dưới dạng key-value.

Mỗi event có thể có tối đa:

```text
50 key-value pairs

```

Custom Event Fields có thể truy cập qua:

```text
Data Export
Data Warehouse

```

nhưng không hiển thị trong AnalyticsIQ standard UI và không có trong Metrics API.

---



# 13.1. Simulator Custom Fields

Nên dùng:

```text
game_mode
difficulty
player_segment
acquisition_source
campaign_id
ab_group

```

Nhưng phải tránh dynamic values vô hạn.

Không nên tạo:

```text
random_uuid
timestamp
random_id

```

làm custom field trên mọi event.

---



# 14. PLAYER MODEL CỦA SIMULATOR

Simulator cần có state cho mỗi player.

Đề xuất:

```text
Player
├── user_id
├── country
├── platform
├── device
├── manufacturer
├── os_version
├── install_date
├── acquisition_source
├── campaign_id
├── player_segment
├── payer_status
├── engagement_segment
├── current_level
├── coins
├── gems
├── energy
├── session_count
├── transaction_num
├── lifetime_revenue
└── churn_status

```

---



# 15. PLAYER SEGMENTS

Không random tất cả player giống nhau.

Đề xuất:

```text
Payer Segment
├── Non-Payer
├── Low-Payer
├── Mid-Payer
└── Whale

```

Engagement:

```text
Engagement
├── Casual
├── Regular
├── Engaged
└── Hardcore

```

Retention:

```text
Lifecycle
├── Early Churn
├── Normal Retention
└── High Retention

```

Các tỷ lệ này là **simulation assumptions**, không phải tỷ lệ thực tế bắt buộc của GameAnalytics.

Phải để chúng trong config để có thể thay đổi.

---



# 16. PLAYER LIFECYCLE

Mỗi player nên đi qua lifecycle:

```text
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

Không phải player nào cũng đi đến Monetization hoặc Retention.

---



# 17. SESSION MODEL

Session:

```text
session_start
      ↓
gameplay
      ↓
progression
      ↓
ads
      ↓
resources
      ↓
purchase
      ↓
session_end

```

Một player có thể có nhiều session:

```text
Player 001
 ├── Session 1
 ├── Session 2
 ├── Session 3
 └── Session 4

```

---



# 18. SESSION DURATION

Không random đều từ:

```text
0 → 172800

```

vì dữ liệu sẽ không thực tế.

Nên xây distribution.

Ví dụ simulation:

```text
Casual:
1–10 minutes

Regular:
10–30 minutes

Engaged:
20–60 minutes

Hardcore:
30–120 minutes

```

Các khoảng này là simulation assumptions và có thể thay đổi.

---



# 19. GAME TIMELINE

Simulator nên mô phỏng nhiều ngày:

```text
Day 0
Day 1
Day 2
...
Day 30

```

Hoặc:

```text
Day 0
...
Day 90

```

Mỗi player có:

```text
install_date

```

Event date được tính từ:

```text
install_date
+
player_age

```

---



# 20. RETENTION MODEL

Player có thể quay lại:

```text
D1
D3
D7
D14
D30

```

Simulator nên tạo cohort có retention khác nhau.

Ví dụ:

```text
Install Date
     ↓
Cohort
     ↓
D1
D3
D7
D14
D30

```

Không nên để tất cả player active mỗi ngày.

---



# 21. PROGRESSION MODEL

Player có:

```text
current_level

```

Progression phụ thuộc vào engagement.

Ví dụ:

```text
Casual
    ↓
ít level

Regular
    ↓
nhiều level

Hardcore
    ↓
rất nhiều level

```

Progression phải có:

```text
Start
Fail
Complete

```

và attempt logic.

---



# 22. MONETIZATION MODEL

Total Revenue:

```text
Total Revenue
=
IAP Revenue
+
Ad Revenue

```



### IAP

Thông qua:

```text
business events

```



### Ads

Thông qua:

```text
ad / impression events

```



### UA Spend

Là nguồn dữ liệu riêng.

Không nên coi:

```text
UA Spend

```

là player purchase event.

---



# 23. IAP PLAYER LOGIC

Không phải player nào cũng mua.

Ví dụ simulation:

```text
Non-Payer
    ↓
không tạo business event

Low-Payer
    ↓
ít business events

Mid-Payer
    ↓
nhiều business events

Whale
    ↓
nhiều business events

```

Các tỷ lệ phải cấu hình được.

---



# 24. PRODUCT CATALOG

Tạo product catalog cố định.

Ví dụ:

```text
GemPack_100
GemPack_500
GemPack_1200
StarterPack
BattlePass
PremiumBundle
MegaBoost
SmallBoost

```

Mỗi product:

```text
product_id
product_type
price
currency

```

Ví dụ:

```json
{
    "product_id": "GemPack_500",
    "product_type": "GemPack",
    "price": 499,
    "currency": "USD"
}

```

---



# 25. AD MODEL

Ad types:

```text
rewarded
interstitial
banner

```

Player behavior:

```text
Casual
    ↓
ít ad

Regular
    ↓
vừa ad

Hardcore
    ↓
nhiều ad

```

Rewarded ad có thể tạo:

```text
Ad Impression
     ↓
Reward
     ↓
Resource Source

```

---



# 26. VIRTUAL ECONOMY MODEL

Player state:

```text
coins
gems
energy

```

Nguồn:

```text
Level Reward
Daily Reward
Quest Reward
Ad Reward
Purchase

```

Sink:

```text
Shop
Upgrade
Energy
Revive
Premium Item

```

Ví dụ:

```text
Level Complete
      ↓
Source Coins +500

Shop Purchase
      ↓
Sink Coins -100

```

---



# 27. ACQUISITION MODEL

Player nên có:

```text
acquisition_source
campaign_id

```

Ví dụ:

```text
organic
facebook
google_ads
tiktok

```

Campaign:

```text
VN_AUG_01
VN_AUG_02
US_AUG_01
JP_AUG_01

```

---



# 28. UA SPEND DATASET

Game events không đủ để tính ROAS.

Simulator/project nên có thêm:

```text
ua_spend.csv

```

Schema:

```text
date
platform
country
source
campaign
spend
impressions
clicks
installs

```

Ví dụ:

```csv
date,platform,country,source,campaign,spend,impressions,clicks,installs
2026-09-01,android,VN,facebook,VN_A,500,120000,4200,850

```

Sau đó:

```text
Game Events
     +
UA Spend
     ↓
Acquisition Analytics
     ↓
CPI
ROAS
LTV:CAC
Payback

```

---



# 29. QUY MÔ SIMULATOR ĐỀ XUẤT

Mục tiêu:

```text
100,000 players

```

Khoảng thời gian:

```text
30–90 days

```

Mục tiêu event:

```text
20–30 million events

```

Phân bổ tham khảo:

```text
Players:
100K

Sessions:
2–3M

Total Events:
20–30M

IAP:
100K–300K

Ad Impressions:
5–10M

Progression:
5–10M

Resource:
5–10M

```

Các con số này là **quy mô thiết kế của Simulator**, không phải giới hạn của GameAnalytics.

---



# 30. KHÔNG LƯU HÀNG CHỤC TRIỆU EVENT TRONG RAM

Không nên:

```python
events = []

for ...:
    events.append(event)

```

cho toàn bộ 20–30M events.

Nên:

```text
Generate
   ↓
Batch
   ↓
Write
   ↓
Send
   ↓
Clear
   ↓
Generate next batch

```

---



# 31. RAW DATA

Simulator nên giữ raw data độc lập với GameAnalytics.

Ví dụ:

```text
data/
└── raw/
    ├── year=2026/
    │   ├── month=09/
    │   │   ├── day=01/
    │   │   │   ├── batch_000001.json.gz
    │   │   │   ├── batch_000002.json.gz
    │   │   │   └── ...

```

Mục đích:

```text
Simulator
   ├──→ GameAnalytics
   │
   └──→ Raw Data Lake

```

Nếu API gặp lỗi:

```text
Raw Data
   ↓
Replay
   ↓
Pipeline

```

---



# 32. PLAYER STATE STORAGE

Nên sử dụng SQLite hoặc database nhỏ để lưu state.

Ví dụ:

```text
simulator.db

```

Bảng:

```text
players
sessions
progression_state
player_economy
queue

```

Thông tin cần persistent:

```text
session_num
transaction_num
current_level
coins
gems
last_session
churn_status

```

---



# 33. EVENT QUEUE STORAGE

Có thể dùng:

```text
SQLite

```

hoặc:

```text
JSONL

```

hoặc:

```text
Parquet

```

cho raw storage.

Khuyến nghị:

```text
SQLite
    → queue/state

JSON/JSONL
    → raw API payload

Parquet
    → analytical raw data

```

---



# 34. EVENT GENERATION PIPELINE

Một event nên đi qua:

```text
Player State
     ↓
Behavior Model
     ↓
Event Generator
     ↓
Default Annotations
     ↓
Event-specific Fields
     ↓
Validator
     ↓
Queue

```

Không:

```text
random()
 ↓
JSON
 ↓
POST

```

---



# 35. VALIDATION PIPELINE

Trước khi gửi:

```text
Event
 ↓
Schema Validation
 ↓
Business Validation
 ↓
Batch Validation

```



### Schema Validation

Kiểm tra:

```text
category
v
user_id
client_ts
sdk_version
os_version
manufacturer
device
platform
session_id
session_num

```



### Business Validation

Ví dụ:

```text
transaction_num tăng
attempt_num hợp lý
session_num tăng
purchase chỉ từ payer
Sink không làm balance âm nếu game rule cấm

```

---



# 36. BATCHING PIPELINE

```text
Events
  ↓
Add Event
  ↓
Serialize JSON
  ↓
Check size
  ↓
< SAFE_LIMIT?
  ├── YES → add next event
  └── NO  → send current batch

```

Mục tiêu:

```text
compressed_payload < 1 MB

```

---



# 37. SEND PIPELINE

```text
Batch
  ↓
JSON encode
  ↓
GZIP
  ↓
HMAC-SHA256
  ↓
Base64
  ↓
Authorization header
  ↓
POST
  ↓
HTTP response

```

---



# 38. RETRY PIPELINE

```text
POST
 │
 ├── SUCCESS
 │      ↓
 │    Mark sent
 │
 └── FAILURE
        ↓
     Retry Queue
        ↓
      Retry
        ↓
     Success

```

Không xóa raw batch trước khi xác nhận request thành công.

---



# 39. COMMON EVENT FACTORY

Nên tạo một function kiểu:

```text
create_default_annotations(player, session)

```

Trả về:

```text
v
user_id
client_ts
sdk_version
os_version
manufacturer
device
platform
session_id
session_num

```

Sau đó event generator chỉ thêm:

```text
category
+
event-specific fields

```

Ví dụ:

```text
default_annotations
       +
business_fields
       ↓
business_event

```

Đây là cách phù hợp với thiết kế default annotations của GameAnalytics.

---



# 40. EVENT GENERATOR MODULES

Nên tách:

```text
generators/
├── player_generator.py
├── session_generator.py
├── progression_generator.py
├── business_generator.py
├── resource_generator.py
├── ad_generator.py
├── design_generator.py
└── error_generator.py

```

Không nên viết toàn bộ simulator trong một file `main.py`.

---



# 41. GAMEANALYTICS CLIENT MODULE

Tách riêng:

```text
gameanalytics/
├── init.py
├── auth.py
├── validator.py
├── batcher.py
├── compressor.py
└── client.py

```



### `auth.py`

Chịu trách nhiệm:

```text
HMAC
Base64
Authorization

```



### `compressor.py`

Chịu trách nhiệm:

```text
GZIP

```



### `batcher.py`

Chịu trách nhiệm:

```text
< 1 MB

```



### `client.py`

Chịu trách nhiệm:

```text
POST
response
retry
logging

```

---



# 42. PROJECT STRUCTURE ĐỀ XUẤT

```text
mobile-game-event-simulator/
│
├── config/
│   ├── game_config.py
│   ├── player_config.py
│   ├── event_config.py
│   └── monetization_config.py
│
├── models/
│   ├── player.py
│   ├── session.py
│   ├── progression.py
│   └── product.py
│
├── generators/
│   ├── player_generator.py
│   ├── session_generator.py
│   ├── progression_generator.py
│   ├── business_generator.py
│   ├── resource_generator.py
│   ├── ad_generator.py
│   ├── design_generator.py
│   └── error_generator.py
│
├── simulation/
│   ├── player_lifecycle.py
│   ├── session_lifecycle.py
│   ├── progression.py
│   ├── economy.py
│   ├── monetization.py
│   └── retention.py
│
├── gameanalytics/
│   ├── init.py
│   ├── annotations.py
│   ├── validator.py
│   ├── batcher.py
│   ├── compressor.py
│   ├── auth.py
│   └── client.py
│
├── storage/
│   ├── sqlite.py
│   ├── queue.py
│   └── raw_writer.py
│
├── data/
│   ├── raw/
│   └── ua_spend/
│
├── .env
├── .gitignore
├── requirements.txt
└── main.py

```

---



# 43. MAIN EXECUTION FLOW

`main.py` nên có logic:

```text
START
  ↓
Load configuration
  ↓
Load GameAnalytics credentials
  ↓
Initialize database
  ↓
Create / load players
  ↓
For each simulation day:
  │
  ├── Determine active players
  │
  ├── Create sessions
  │
  ├── Create user event
  │
  ├── Generate gameplay
  │
  ├── Generate progression
  │
  ├── Generate ads
  │
  ├── Generate resources
  │
  ├── Generate IAP
  │
  ├── Generate design events
  │
  ├── Generate rare errors
  │
  ├── Generate session_end
  │
  ├── Validate
  │
  ├── Write raw
  │
  ├── Build batches
  │
  ├── GZIP
  │
  ├── HMAC
  │
  └── POST GameAnalytics
  │
  ↓
Next simulation day
  ↓
FINISH

```

---



# 44. EVENT RELATIONSHIP RULE

Đây là rule quan trọng nhất của Simulator.

Event phải phản ánh Player State.

Ví dụ:

```text
Player = Hardcore + Whale
       ↓
High session frequency
       ↓
High progression
       ↓
High ad exposure
       ↓
High purchase probability
       ↓
High lifetime revenue

```

Ngược lại:

```text
Player = Casual + Non-Payer
       ↓
Few sessions
       ↓
Low progression
       ↓
Few ads
       ↓
No IAP
       ↓
Possible early churn

```

Không random từng event độc lập.

---



# 45. DATA QUALITY RULES

Simulator phải tạo được:

### Referential consistency

```text
user_id tồn tại
session_id tồn tại
product tồn tại
level tồn tại

```



### Temporal consistency

```text
install
 <
session_start
 <
gameplay
 <
session_end

```



### Session consistency

```text
session_num tăng
session_id thay đổi

```



### Purchase consistency

```text
transaction_num tăng
amount > 0
currency hợp lệ

```



### Progression consistency

```text
Start
 →
Fail / Complete

```



### Economy consistency

```text
Source
 →
Balance tăng

Sink
 →
Balance giảm

```

---



# 46. EVENT VOLUME RULE

Không nên tạo event volume bằng cách:

```text
random number of events

```

mà nên dựa vào:

```text
Player Segment
+
Session Duration
+
Session Frequency
+
Level
+
Monetization Status

```

Ví dụ:

```text
engagement_score
       ↓
session_probability
       ↓
session_count
       ↓
event_count

```

---



# 47. COHORT MODEL

Mỗi player phải có:

```text
install_date

```

Từ đó:

```text
install_date
      ↓
cohort
      ↓
player_age
      ↓
retention
      ↓
revenue
      ↓
LTV

```

---



# 48. METRICS MÀ DATASET PHẢI HỖ TRỢ



## Acquisition

```text
Installs
Clicks
Impressions
CTR
CPI

```



## Engagement

```text
DAU
WAU
MAU
Sessions
Sessions/User
Session Duration

```



## Retention

```text
D1
D3
D7
D14
D30

```



## Progression

```text
Level Starts
Level Completes
Level Fails
Completion Rate
Failure Rate
Average Attempts

```



## IAP

```text
Revenue
Transactions
Paying Users
ARPU
ARPPU
IAP Conversion

```



## Ads

```text
Impressions
Ad Revenue
eCPM
Ad ARPDAU

```



## Economy

```text
Coins Source
Coins Sink
Gems Source
Gems Sink
Net Economy Flow

```



## LTV

```text
D0 LTV
D1 LTV
D7 LTV
D30 LTV

```



## UA

```text
Spend
Installs
CPI
Revenue
ROAS
LTV:CAC
Payback

```

---



# 49. CORE DATA MODEL SAU KHI TRANSFORM

Raw:

```text
events
players
ua_spend

```

Core:

```text
dim_player
dim_date

fact_session
fact_progression
fact_purchase
fact_ad_impression
fact_resource
fact_design
fact_error

```

Marts:

```text
mart_daily_engagement
mart_daily_monetization
mart_retention
mart_cohort
mart_ltv
mart_ua_performance
mart_player_economy

```

---



# 50. PIPELINE CUỐI CÙNG

```text
                 MOBILE GAME EVENT SIMULATOR
                              │
                              ▼
                         Raw Events
                              │
                  ┌───────────┴───────────┐
                  │                       │
                  ▼                       ▼
          GameAnalytics API          Local Raw Data
                  │                       │
                  ▼                       │
          GA Data Export                 │
                  │                       │
                  └───────────┬───────────┘
                              ▼
                          Data Lake
                              │
                              ▼
                           Airflow
                              │
                              ▼
                             dbt
                              │
                              ▼
                       Data Warehouse
                              │
                              ▼
                           Power BI

```

---



# 51. CHECKLIST — GAMEANALYTICS API

Trước khi gửi event:

```text
[ ] HTTPS
[ ] Correct GAME_KEY
[ ] Correct SECRET_KEY
[ ] Sandbox / Production đúng
[ ] POST /v2/<game_key>/events
[ ] Content-Type = application/json
[ ] Body là JSON list
[ ] category hợp lệ
[ ] v = 2
[ ] user_id hợp lệ
[ ] client_ts hợp lệ
[ ] sdk_version = rest api v2
[ ] os_version hợp lệ
[ ] manufacturer hợp lệ
[ ] device hợp lệ
[ ] platform hợp lệ
[ ] session_id là UUID
[ ] session_num >= 1
[ ] HMAC SHA-256
[ ] Base64 Authorization
[ ] Nếu gzip → HMAC trên gzipped body
[ ] Payload < 1 MB
[ ] Retry mechanism
[ ] Queue mechanism

```

---



# 52. CHECKLIST — SIMULATOR

```text
[ ] Player Generator
[ ] Player State
[ ] Player lifecycle
[ ] Install date
[ ] Acquisition source
[ ] Campaign
[ ] Platform
[ ] Country
[ ] Device
[ ] Session generator
[ ] Session start
[ ] Session end
[ ] Session duration
[ ] Progression
[ ] Attempt logic
[ ] IAP
[ ] Transaction counter
[ ] Product catalog
[ ] Ads
[ ] Impression
[ ] Resource economy
[ ] Source / Sink
[ ] Design events
[ ] Rare errors
[ ] Custom fields
[ ] Retention
[ ] Churn
[ ] Cohort
[ ] UA Spend
[ ] Raw storage
[ ] Queue
[ ] Validator
[ ] Batcher
[ ] Compression
[ ] HMAC
[ ] API client
[ ] Retry
[ ] Logging

```

---



# 53. CHECKLIST — DATA REALISM

```text
[ ] Không random event độc lập
[ ] Player có state
[ ] Player có lifecycle
[ ] Player có behavioral segment
[ ] Session liên quan player
[ ] Session duration hợp lý
[ ] Progression liên quan session
[ ] Progression có Start/Fail/Complete
[ ] attempt_num hợp lý
[ ] Purchase liên quan payer
[ ] transaction_num tăng dần
[ ] Ad liên quan session
[ ] Resource liên quan gameplay
[ ] Source/Sink có logic
[ ] Churn tồn tại
[ ] Retention tồn tại
[ ] Có cohort
[ ] Có acquisition source
[ ] Không có quá nhiều unique event IDs
[ ] Không spam resource events
[ ] Không spam error events
[ ] Có raw data
[ ] Có thể replay pipeline

```

---



# 54. NGUYÊN TẮC THIẾT KẾ QUAN TRỌNG NHẤT

Simulator phải được xây dựng theo:

```text
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

Không xây theo:

```text
random()
   ↓
random()
   ↓
random()
   ↓
JSON
   ↓
API

```

---



# 55. PHÂN BIỆT API RULE VÀ SIMULATOR RULE



## GameAnalytics API quyết định:

```text
HTTPS
JSON
POST
Game Key
Secret Key
HMAC
API v2
Event categories
Event schema
Default annotations
1 MB POST limit
HTTP response
Queue recommendation
Init

```



## Simulator quyết định:

```text
Số player
Số ngày
Số event
Player segments
Engagement distribution
Payer distribution
Retention distribution
Session distribution
Progression model
IAP model
Ad model
Economy model
Acquisition model
UA Spend
Churn model

```

Đây là hai lớp khác nhau:

```text
┌──────────────────────────────┐
│       GameAnalytics API      │
│                              │
│    TECHNICAL CONTRACT        │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│     Mobile Game Simulator    │
│                              │
│    BUSINESS / BEHAVIOR       │
│         SIMULATION            │
└──────────────────────────────┘

```

---



# 56. PHIÊN BẢN MVP NÊN TRIỂN KHAI

Không nên ngay lập tức xây 30 triệu events.

Phase 1:

```text
100 players
1 day

```

Test:

```text
user
session_end
progression
business
resource

```

↓

Phase 2:

```text
1,000 players
7 days

```

Test:

```text
retention
IAP
ads
economy

```

↓

Phase 3:

```text
10,000 players
30 days

```

Test:

```text
pipeline
batching
retry
raw storage

```

↓

Phase 4:

```text
100,000 players
30–90 days
20–30M events

```

Test:

```text
full Data Engineering pipeline

```

---



# 57. THỨ TỰ CODE KHUYẾN NGHỊ

Để tránh viết lại project nhiều lần, triển khai theo thứ tự:

```text
1. Configuration
        ↓
2. Player Model
        ↓
3. Player Generator
        ↓
4. Player State Storage
        ↓
5. Session Model
        ↓
6. Common Annotations
        ↓
7. User Event
        ↓
8. Session End Event
        ↓
9. Progression Event
        ↓
10. Resource Event
        ↓
11. Business Event
        ↓
12. Ad / Impression Event
        ↓
13. Design Event
        ↓
14. Error Event
        ↓
15. Event Validator
        ↓
16. Event Queue
        ↓
17. Batcher
        ↓
18. GZIP
        ↓
19. HMAC
        ↓
20. GameAnalytics Client
        ↓
21. Retry
        ↓
22. Raw Data Writer
        ↓
23. Retention / Churn
        ↓
24. UA Spend
        ↓
25. Large-scale simulation

```

---



# 58. REFERENCE SOURCES

Official GameAnalytics documentation:

1. Collection API — Setup
2. Collection API — Event Types
3. Custom Event Fields
4. PipelineIQ — Data Export JSON Schemas

Các tài liệu trên là nguồn tham chiếu chính để kiểm tra API contract và schema khi triển khai Simulator.

---



# 59. FINAL DESIGN

Simulator hoàn chỉnh:

```text
                         ┌───────────────────────┐
                         │       PLAYERS         │
                         │       100K            │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │    PLAYER STATE       │
                         │                       │
                         │ engagement            │
                         │ progression           │
                         │ economy               │
                         │ monetization          │
                         │ retention             │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   EVENT GENERATORS    │
                         ├───────────────────────┤
                         │ user                  │
                         │ session_end           │
                         │ progression           │
                         │ business              │
                         │ resource              │
                         │ ad                    │
                         │ impression            │
                         │ design                │
                         │ error                 │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │       VALIDATOR       │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │        QUEUE          │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │       BATCHER         │
                         │       < 1 MB          │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │        GZIP           │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │        HMAC           │
                         │      SHA-256          │
                         └───────────┬───────────┘
                                     │
                                     ▼
                     ┌──────────────────────────────┐
                     │  GAMEANALYTICS COLLECTION API│
                     └──────────────┬───────────────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                         ▼                     ▼
                    GA Analytics          Data Export
                                              │
                                              ▼
                                         JSON/Parquet
                                              │
                                              ▼
                                           Data Lake
                                              │
                                              ▼
                                           Airflow
                                              │
                                              ▼
                                             dbt
                                              │
                                              ▼
                                        Data Warehouse
                                              │
                                              ▼
                                           Power BI

```



## FINAL PRINCIPLE

> **Mobile Game Event Simulator không phải là random data generator.**

Nó phải mô phỏng một **game stateful system**, trong đó:

```text
Player
  ↓
Behavior
  ↓
Session
  ↓
Gameplay
  ↓
Progression
  ↓
Economy
  ↓
Monetization
  ↓
Retention / Churn

```

và sau đó chuyển các trạng thái đó thành **GameAnalytics-compliant events**:

```text
Game State
    ↓
Event Model
    ↓
GameAnalytics Schema
    ↓
Queue
    ↓
Batch < 1 MB
    ↓
GZIP
    ↓
HMAC-SHA256
    ↓
Collection API

```

Mục tiêu cuối cùng là tạo ra một dataset mà từ đó có thể xây dựng một hệ thống:

```text
ACQUISITION
     ↓
ENGAGEMENT
     ↓
PROGRESSION
     ↓
MONETIZATION
     ↓
RETENTION
     ↓
LTV
     ↓
ROAS

```

thay vì chỉ tạo ra hàng triệu dòng dữ liệu ngẫu nhiên.

## NEXT PROMPT
Test quá trình lấy data về từ raw và phân tích