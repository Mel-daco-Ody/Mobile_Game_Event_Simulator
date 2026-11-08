"""Cấu hình player.

Định nghĩa các phân bổ cho player segments, acquisition source,
country, platform, device. Toàn bộ phải cấu hình được, không hard-code.
"""

# === Payer segment ===
# Phân bổ player theo khả năng chi trả
PAYER_SEGMENTS = ["non_payer", "low_payer", "mid_payer", "whale"]
PAYER_SEGMENT_WEIGHTS = [0.70, 0.20, 0.08, 0.02]

# === Engagement segment ===
# Engagement phụ thuộc vào tần suất chơi & thời lượng mỗi session
ENGAGEMENT_SEGMENTS = ["casual", "regular", "engaged", "hardcore"]
ENGAGEMENT_SEGMENT_WEIGHTS = [0.40, 0.35, 0.20, 0.05]

# === Lifecycle / Churn segment ===
# Cohort retention khác nhau theo lifecycle
LIFECYCLE_SEGMENTS = ["early_churn", "normal_retention", "high_retention"]
LIFECYCLE_SEGMENT_WEIGHTS = [0.40, 0.45, 0.15]

# === Acquisition source ===
ACQUISITION_SOURCES = ["organic", "facebook", "google_ads", "tiktok"]
ACQUISITION_SOURCE_WEIGHTS = [0.40, 0.25, 0.25, 0.10]

# === Campaign theo acquisition source ===
# Mỗi campaign gắn với một quốc gia & giai đoạn để mô phỏng UA
CAMPAIGNS = {
    "organic": [None],  # organic không có campaign id
    "facebook": ["VN_AUG_01", "US_AUG_01", "JP_AUG_01", "VN_SEP_01"],
    "google_ads": ["VN_AUG_02", "US_AUG_02", "JP_AUG_02", "KR_AUG_01"],
    "tiktok": ["VN_AUG_03", "ID_AUG_01", "TH_AUG_01"],
}

# === Tỉ lệ các chỉ số hành vi theo engagement ===
# Session frequency trung bình mỗi tuần
SESSION_FREQUENCY_PER_WEEK = {
    "casual": (1, 3),
    "regular": (3, 7),
    "engaged": (7, 14),
    "hardcore": (14, 30),
}

# Xác suất player active trong một ngày bất kỳ
DAILY_ACTIVE_PROBABILITY = {
    "early_churn": 0.05,
    "normal_retention": 0.35,
    "high_retention": 0.70,
}

# Retention checkpoints (ngày kể từ install_date)
RETENTION_CHECKPOINTS = [1, 3, 7, 14, 30]

# === Resource (virtual economy) khởi tạo cho mỗi player ===
# Balance ban đầu trước khi bắt đầu session đầu tiên
INITIAL_PLAYER_BALANCE = {
    "coins": 0,
    "gems": 50,        # một ít gem khởi đầu
    "energy": 30,      # năng lượng
    "lives": 5,
    "stars": 0,
    "tickets": 1,
}

# === Level bắt đầu ===
INITIAL_PLAYER_LEVEL = 1

# === World/Level hierarchy cho progression ===
WORLD_COUNT = 5                        # World1 → World5
LEVELS_PER_WORLD = 20                  # Level01 → Level20
MAX_LEVEL = WORLD_COUNT * LEVELS_PER_WORLD

# Speed progression theo engagement (level/ngày)
LEVEL_PROGRESSION_SPEED = {
    "casual": (0.5, 1.5),
    "regular": (1, 3),
    "engaged": (2, 5),
    "hardcore": (4, 8),
}

# Xác suất thành công mỗi attempt progression
ATTEMPT_SUCCESS_PROBABILITY = {
    "casual": 0.55,
    "regular": 0.65,
    "engaged": 0.75,
    "hardcore": 0.85,
}

# Số attempt tối đa trước khi tự abandon (bỏ qua level hiện tại)
MAX_ATTEMPTS_PER_LEVEL = 5
