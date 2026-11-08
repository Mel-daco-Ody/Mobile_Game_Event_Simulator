"""Cấu hình retention / churn.

Theo BƯỚC 23 INSTRUCTION.md:

    **Mục tiêu:** Mô hình hoá lifecycle đầy đủ của player.

    Lifecycle::

        ACQUISITION
          ↓
        INSTALL
          ↓
        FIRST_SESSION
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

    Mỗi player có ``install_date``.
    Tính ``player_age = simulation_date - install_date``.

    Cohort & retention checkpoints::

        D1, D3, D7, D14, D30

    Mỗi cohort có retention curve khác nhau
    (Early Churn / Normal / High Retention).

    **Quy tắc:** KHÔNG để tất cả player active mỗi ngày —
    phải có cả churn và dormant.

Module này cung cấp:

    - ``LIFECYCLE_STAGES``: 8 stages của player lifecycle
    - ``RETENTION_CHECKPOINTS``: [1, 3, 7, 14, 30]
    - ``RETENTION_CURVES``: dict churn_segment → curve 5 điểm
    - ``DAILY_ACTIVE_PROBABILITY_OVERRIDE``: override cho inactive decay
"""

# =====================================================================
# Lifecycle stages (theo INSTRUCTION)
# =====================================================================
LIFECYCLE_STAGES: tuple[str, ...] = (
    "acquisition",     # User vừa được acquire (install intent)
    "install",         # App đã download + first launch
    "first_session",   # Session đầu tiên đã chơi
    "activation",      # Hoàn thành tutorial + unlocks game loop
    "engagement",      # Đang chơi đều đặn
    "monetization",    # Đã thực hiện 1+ IAP
    "retention",       # Đã vượt qua D30 → loyal player
    "churn",           # Không còn active (status=churned)
)

# Stage transition rules (từ → list các stage có thể đi tới)
LIFECYCLE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "acquisition": ("install",),
    "install": ("first_session",),
    "first_session": ("activation", "engagement"),  # có thể churn sớm
    "activation": ("engagement",),
    "engagement": ("monetization", "retention", "churn"),
    "monetization": ("retention", "engagement", "churn"),
    "retention": ("engagement", "churn"),
    "churn": (),  # terminal
}

# =====================================================================
# Retention checkpoints (theo INSTRUCTION)
# =====================================================================
RETENTION_CHECKPOINTS: tuple[int, ...] = (1, 3, 7, 14, 30)

# Số ngày giữa các checkpoints (dùng để tính decay giữa checkpoints)
# D1→D3: 2 ngày, D3→D7: 4 ngày, D7→D14: 7 ngày, D14→D30: 16 ngày
CHECKPOINT_INTERVALS: tuple[int, ...] = (
    1,  # D1 (sau 1 ngày)
    2,  # D3 (D1 → D3 = 2 ngày)
    4,  # D7 (D3 → D7 = 4 ngày)
    7,  # D14
    16, # D30
)

# =====================================================================
# Retention curves cho 3 churn_segment
# =====================================================================
# Format: {churn_segment: {day: retention_rate}}
# retention_rate = % players còn active tại checkpoint đó
#
# Đảm bảo: rate giảm dần theo thời gian (không đảo chiều).

RETENTION_CURVES: dict[str, dict[int, float]] = {
    # Early Churn: 40% player (cao nhất). Rời bỏ nhanh.
    "early_churn": {
        1: 0.20,
        3: 0.10,
        7: 0.05,
        14: 0.02,
        30: 0.01,
    },
    # Normal Retention: 45% player (default). Curve trung bình.
    "normal_retention": {
        1: 0.50,
        3: 0.35,
        7: 0.25,
        14: 0.18,
        30: 0.12,
    },
    # High Retention: 15% player. Loyalty cao.
    "high_retention": {
        1: 0.70,
        3: 0.55,
        7: 0.45,
        14: 0.40,
        30: 0.30,
    },
}

# =====================================================================
# Daily active probability (dùng giữa các checkpoint)
# =====================================================================
# Sau khi vượt qua retention checkpoint threshold, xác suất player
# active trong một ngày bất kỳ ≈ giá trị này.

DAILY_ACTIVE_PROBABILITY: dict[str, float] = {
    "early_churn": 0.05,
    "normal_retention": 0.35,
    "high_retention": 0.70,
}

# =====================================================================
# Lifecycle stage thresholds (khi nào transition stage)
# =====================================================================
# Mỗi stage transition dựa trên player_age + retention rate hiện tại.
# Có thể override tại đây nếu muốn.

# Age (ngày) mà player được coi là "active" nếu vẫn chơi
ACTIVATION_AGE_DAYS: int = 0      # Ngay sau first_session
ENGAGEMENT_AGE_DAYS: int = 7      # Sau 7 ngày
RETENTION_THRESHOLD_AGE_DAYS: int = 30  # Sau 30 ngày = loyal

# Xác suất player trong engagement thực hiện IAP (enters monetization)
MONETIZATION_PROBABILITY: float = 0.30  # 30% engaged player sẽ IAP

# =====================================================================
# Cohort analysis
# =====================================================================
# Dùng để group player theo install_date cho retention report

COHORT_DATE_FORMAT: str = "%Y-%m-%d"  # "2026-09-28"

# =====================================================================
# Churn status constants (mirror Player model)
# =====================================================================
CHURN_STATUS_ACTIVE: str = "active"
CHURN_STATUS_DORMANT: str = "dormant"  # Inactive một thời gian, có thể quay lại
CHURN_STATUS_CHURNED: str = "churned"  # Đã rời game hoàn toàn

# Số ngày không active trước khi chuyển từ active → dormant
DORMANT_AFTER_DAYS: int = 3

# Số ngày không active trước khi chuyển từ dormant → churned
CHURNED_AFTER_DAYS: int = 14
