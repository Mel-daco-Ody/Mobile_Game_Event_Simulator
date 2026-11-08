"""Cấu hình event generation.

Các tham số về tỉ lệ error, custom fields, ad theo engagement.
"""

# === Error event ===
# GameAnalytics cảnh báo KHÔNG gửi quá nhiều error events
# Nên đặt xác suất rất thấp
ERROR_PROBABILITY_PER_EVENT = 0.001  # 0.1%

# Số error event tối đa mỗi session
MAX_ERROR_EVENTS_PER_SESSION = 1

# Error severity phân bổ
ERROR_SEVERITY_LEVELS = ["debug", "info", "warning", "error", "critical"]
ERROR_SEVERITY_WEIGHTS = [0.40, 0.30, 0.20, 0.08, 0.02]

# Một số error message mẫu (giữ tập trung, KHÔNG random vô hạn)
ERROR_MESSAGES = [
    "network_timeout",
    "asset_load_failed",
    "save_sync_failed",
    "config_parse_error",
    "unexpected_null_reference",
]

# === Ad events ===
# Ad types: rewarded, interstitial, banner
AD_TYPES = ["rewarded", "interstitial", "banner"]
AD_TYPE_WEIGHTS = [0.30, 0.50, 0.20]

# Xác suất hiển thị ad trong session theo engagement
AD_PROBABILITY_PER_SESSION = {
    "casual": 0.40,
    "regular": 0.65,
    "engaged": 0.80,
    "hardcore": 0.90,
}

# Rewarded ad → coin reward
REWARDED_AD_COIN_REWARD_RANGE = (50, 200)

# eCPM giả lập theo ad type (USD)
ECPM_BY_AD_TYPE = {
    "rewarded": 5.0,
    "interstitial": 2.0,
    "banner": 0.5,
}

# === Resource events ===
# Resource flow types
RESOURCE_FLOW_TYPES = ["Source", "Sink"]

# Resource currencies
RESOURCE_CURRENCIES = ["Coins", "Gems", "Energy", "Lives", "Stars", "Tickets"]

# Sink/Source items per currency.
# Mỗi item là tuple (item_type, item_id) để match INSTRUCTION format:
#     [flowType]:[virtualCurrency]:[itemType]:[itemId]
# Ví dụ: ("Gameplay", "LevelReward") -> "Source:Coins:Gameplay:LevelReward"
RESOURCE_ITEMS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "Coins": {
        "source": [
            ("Gameplay", "LevelReward"),
            ("Gameplay", "DailyReward"),
            ("Gameplay", "QuestReward"),
            ("Ad", "Rewarded"),
        ],
        "sink": [
            ("Shop", "HealthPotion"),
            ("Shop", "ContinueRun"),
            ("Upgrade", "Tower"),
            ("Revive", "Level"),
        ],
    },
    "Gems": {
        "source": [
            ("Purchase", "GemPack"),
            ("Gameplay", "Achievement"),
        ],
        "sink": [
            ("Shop", "PremiumItem"),
            ("Shop", "BattlePass"),
            ("Shop", "MegaBoost"),
            ("Shop", "SkipLevel"),
        ],
    },
    "Energy": {
        "source": [
            ("Gameplay", "DailyReward"),
            ("Purchase", "EnergyRefill"),
        ],
        "sink": [
            ("Gameplay", "LevelEntry"),
            ("Revive", "Level"),
        ],
    },
    "Lives": {
        "source": [
            ("Gameplay", "DailyReward"),
            ("Shop", "ExtraLife"),
        ],
        "sink": [
            ("Revive", "Level"),
            ("Continue", "Level"),
        ],
    },
    "Stars": {
        "source": [
            ("Gameplay", "LevelComplete"),
            ("Gameplay", "Achievement"),
        ],
        "sink": [
            ("Shop", "StarBundle"),
        ],
    },
    "Tickets": {
        "source": [
            ("Gameplay", "DailyReward"),
            ("Purchase", "TicketBundle"),
        ],
        "sink": [
            ("Gameplay", "EventEntry"),
        ],
    },
}

# === Resource amount range theo currency/flow/item ===
# Phục vụ cho "amount bao nhiêu" mỗi lần Source/Sink.
# Mỗi entry là (min, max) – số nguyên dương; Simulator sẽ đổi dấu cho Sink.
# Match INSTRUCTION: "+500 Coins" là 1 event với amount=500 (không tạo 500 event).
RESOURCE_AMOUNT_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "Coins": {
        "Source:Gameplay:LevelReward":    (100, 500),    # per level reward
        "Source:Gameplay:DailyReward":    (200, 800),
        "Source:Gameplay:QuestReward":    (50,  300),
        "Source:Ad:Rewarded":             (50,  200),
        "Sink:Shop:HealthPotion":         (50,  150),
        "Sink:Shop:ContinueRun":          (100, 200),
        "Sink:Upgrade:Tower":             (200, 500),
        "Sink:Revive:Level":              (75,  150),
    },
    "Gems": {
        "Source:Purchase:GemPack":        (10,  50),     # phần thưởng bonus đi kèm
        "Source:Gameplay:Achievement":    (5,   20),
        "Sink:Shop:PremiumItem":          (50,  200),
        "Sink:Shop:BattlePass":           (100, 250),
        "Sink:Shop:MegaBoost":            (20,  60),
        "Sink:Shop:SkipLevel":            (5,   15),
    },
    "Energy": {
        "Source:Gameplay:DailyReward":    (15,  30),
        "Source:Purchase:EnergyRefill":   (20,  50),
        "Sink:Gameplay:LevelEntry":       (1,   3),
        "Sink:Revive:Level":              (5,   10),
    },
    "Lives": {
        "Source:Gameplay:DailyReward":    (1,   3),
        "Source:Shop:ExtraLife":          (1,   3),
        "Sink:Revive:Level":              (1,   1),
        "Sink:Continue:Level":            (1,   1),
    },
    "Stars": {
        "Source:Gameplay:LevelComplete":  (1,   3),
        "Source:Gameplay:Achievement":    (5,   15),
        "Sink:Shop:StarBundle":           (10,  30),
    },
    "Tickets": {
        "Source:Gameplay:DailyReward":    (1,   2),
        "Source:Purchase:TicketBundle":   (3,   10),
        "Sink:Gameplay:EventEntry":       (1,   1),
    },
}


def resource_amount_range(currency: str, flow_type: str,
                          item_type: str, item_id: str) -> tuple[int, int]:
    """Tra cứu amount range cho 1 (currency, flow_type, item_type, item_id).

    Trả về (min, max) số nguyên dương. Mặc định (1, 1) nếu chưa cấu hình.
    """
    key = f"{flow_type}:{item_type}:{item_id}"
    return RESOURCE_AMOUNT_RANGES.get(currency, {}).get(key, (1, 1))

# === Design events ===
# Tập event_id cố định, KHÔNG tạo dynamic ID
DESIGN_EVENT_IDS = [
    "Gameplay:Kill:Zombie",
    "Gameplay:Kill:Boss",
    "Gameplay:Kill:Tank",
    "Gameplay:Kill:Ranger",
    "Gameplay:Weapon:Upgrade",
    "Gameplay:Skill:Unlock",
    "Gameplay:Shop:Open",
    "Gameplay:Shop:Close",
    "Gameplay:Quest:Complete",
    "Gameplay:Achievement:Unlock",
    "Gameplay:Chest:Open",
    "Gameplay:DailyReward:Claim",
]

# Custom event fields hữu ích (giữ tập giới hạn, không random vô hạn)
# Theo section 13.1 INSTRUCTION
CUSTOM_FIELDS = {
    "game_mode": ["story", "endless", "pvp", "event", "tutorial"],
    "difficulty": ["easy", "normal", "hard", "expert"],
    "ab_group": ["A", "B", "C"],
}

# Xác suất gắn custom field vào event
CUSTOM_FIELD_PROBABILITY = 0.30

# === Design event value mapping ===
# Mỗi event_id có 1 "natural" value field (numeric, dynamic data).
# Mục tiêu: KHÔNG tạo dynamic event_id, chỉ dùng value để gắn data.
# Format: event_id -> (val_min, val_max, val_meaning_description)
DESIGN_VALUE_RANGES: dict[str, tuple[int, int, str]] = {
    # Kill events → damage trên 1 kill
    "Gameplay:Kill:Zombie":        (10,  80,   "damage"),
    "Gameplay:Kill:Boss":          (100, 1000, "damage"),
    "Gameplay:Kill:Tank":          (50,  300,  "damage"),
    "Gameplay:Kill:Ranger":        (30,  150,  "damage"),
    # Upgrade/Unlock → new level
    "Gameplay:Weapon:Upgrade":     (1,   5,    "new_level"),
    "Gameplay:Skill:Unlock":       (1,   3,    "skill_tier"),
    # Shop → số item viewed/purchased
    "Gameplay:Shop:Open":          (1,   12,   "items_viewed"),
    "Gameplay:Shop:Close":         (1,   12,   "items_viewed"),
    # Quest/Achievement → reward amount (Coins)
    "Gameplay:Quest:Complete":     (100, 500,  "coin_reward"),
    "Gameplay:Achievement:Unlock": (200, 1000, "coin_reward"),
    # Chest → rarity (1=common..5=legendary)
    "Gameplay:Chest:Open":         (1,   5,    "rarity"),
    # Daily reward → streak day
    "Gameplay:DailyReward:Claim":  (1,   7,    "streak_day"),
}

# Xác suất phát design event trong session theo engagement_segment
# Casual ít event, hardcore nhiều event.
DESIGN_EVENT_PROBABILITY_PER_SESSION: dict[str, float] = {
    "casual":   0.30,
    "regular":  0.50,
    "engaged":  0.70,
    "hardcore": 0.85,
}
