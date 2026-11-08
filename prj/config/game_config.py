"""Cấu hình game tổng thể.

Tập trung mọi tham số về game, API endpoint, build & SDK version.
Toàn bộ con số là simulation assumption, có thể điều chỉnh trong file này.
"""

# === Thông tin GameAnalytics SDK ===
# API version hiện tại của GameAnalytics Collection API
API_VERSION = "v2"

# Phiên bản SDK cố định cho custom solution (KHÔNG tự đặt khác)
SDK_VERSION = "rest api v2"

# Phiên bản build mô phỏng của game
GAME_BUILD = "1.0.0"

# Số version event theo GameAnalytics schema
EVENT_VERSION = 2

# === GameAnalytics API endpoint ===
# Lấy từ biến môi trường để chuyển đổi sandbox/production
# - sandbox: https://sandbox-api.gameanalytics.com
# - production: https://api.gameanalytics.com
GA_BASE_URLS = {
    "sandbox": "https://sandbox-api.gameanalytics.com",
    "production": "https://api.gameanalytics.com",
}

# Hai route chính của Collection API
INIT_ENDPOINT = "/v2/{game_key}/init"
EVENTS_ENDPOINT = "/v2/{game_key}/events"

# === Payload size limit ===
# GameAnalytics giới hạn POST size = 1 MB
POST_LIMIT_BYTES = 1 * 1024 * 1024  # 1 MB

# SAFE_LIMIT thấp hơn 1 MB để có margin an toàn (khoảng 90%)
SAFE_LIMIT_BYTES = 900 * 1024  # 900 KB

# === Connection type (mobile simulator ưu tiên wifi/wwan) ===
CONNECTION_TYPES = ["wifi", "wwan"]
CONNECTION_TYPE_WEIGHTS = [0.7, 0.3]  # 70% wifi, 30% wwan

# === Platforms hợp lệ ===
# Theo GameAnalytics, mobile simulator nên dùng android/ios
PLATFORMS = ["android", "ios"]
PLATFORM_WEIGHTS = [0.6, 0.4]  # 60% android, 40% ios

# === OS versions theo platform ===
OS_VERSIONS = {
    "android": ["android 13", "android 14", "android 15"],
    "ios": ["ios 17.6", "ios 18.0", "ios 18.6", "ios 19"],
}

# === Manufacturers ===
MANUFACTURERS_BY_PLATFORM = {
    "android": ["samsung", "xiaomi", "oppo", "vivo", "google"],
    "ios": ["apple"],
}

# === Device models (giữ độ dài hợp lý) ===
DEVICE_MODELS = {
    "android": ["SM-S918B", "SM-A556E", "Pixel 8", "Pixel 9", "Mi 14", "Vivo X100"],
    "ios": ["iPhone15,2", "iPhone15,3", "iPhone16,1", "iPhone17,1"],
}

# === Country pool cho player mô phỏng ===
COUNTRIES = ["VN", "US", "JP", "KR", "GB", "DE", "FR", "BR", "ID", "TH"]
COUNTRY_WEIGHTS = [0.30, 0.20, 0.10, 0.08, 0.07, 0.06, 0.05, 0.05, 0.05, 0.04]

# === Quy mô simulation mặc định ===
# Có thể đổi để chạy Phase 1 (100) → Phase 4 (100K)
DEFAULT_PLAYER_COUNT = 100
DEFAULT_SIMULATION_DAYS = 30

# Session duration mặc định (giây) theo engagement segment
SESSION_DURATION_RANGE_SECONDS = {
    "casual": (60, 600),       # 1–10 phút
    "regular": (600, 1800),    # 10–30 phút
    "engaged": (1200, 3600),   # 20–60 phút
    "hardcore": (1800, 7200),  # 30–120 phút
}

# Theo rule GameAnalytics: 0 <= session_length <= 172800 giây (48h)
SESSION_MAX_LENGTH_SECONDS = 172800

# === Time zone / timestamp ===
# Unix epoch làm client_ts (KHÔNG dùng ISO string)
USE_UNIX_TIMESTAMP = True
