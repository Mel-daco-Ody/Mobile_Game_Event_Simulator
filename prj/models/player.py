"""Player model – định nghĩa cấu trúc dữ liệu cho một player.

Theo BƯỚC 2 trong INSTRUCTION.md (section 14 của README):
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
    ├── player_segment        (payer)
    ├── engagement_segment
    ├── churn_segment         (lifecycle)
    ├── current_level
    ├── coins
    ├── gems
    ├── energy
    ├── session_count
    ├── transaction_num
    ├── lifetime_revenue
    └── churn_status

Model này dùng dataclass (chuẩn Python, không cần thêm dependency).
Sẽ được dùng xuyên suốt bởi player_generator, session_generator, ...
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# === Enum dạng string để dễ serialize/đọc từ DB ===
# Không dùng enum.Enum cứng để tránh phải convert khi ghi DB / JSON

PAYER_SEGMENTS: tuple[str, ...] = ("non_payer", "low_payer", "mid_payer", "whale")
ENGAGEMENT_SEGMENTS: tuple[str, ...] = ("casual", "regular", "engaged", "hardcore")
LIFECYCLE_SEGMENTS: tuple[str, ...] = ("early_churn", "normal_retention", "high_retention")
ACQUISITION_SOURCES: tuple[str, ...] = ("organic", "facebook", "google_ads", "tiktok")

# Trạng thái churn cuối cùng – sau khi lifecycle early_churn kết thúc
CHURN_STATUS_ACTIVE: str = "active"
CHURN_STATUS_DORMANT: str = "dormant"
CHURN_STATUS_CHURNED: str = "churned"
CHURN_STATUSES: tuple[str, ...] = (CHURN_STATUS_ACTIVE, CHURN_STATUS_DORMANT, CHURN_STATUS_CHURNED)


@dataclass
class Player:
    """Mô hình một player trong simulation.

    Lưu ý:
        - user_id ổn định xuyên suốt mọi session của player.
        - transaction_num chỉ tăng, không reset mỗi session.
        - lifetime_revenue lưu theo đơn vị nhỏ nhất của currency (minor unit)
          để thống nhất với business event amount.
    """

    # === Định danh ===
    user_id: str

    # === Phân loại thiết bị ===
    country: str
    platform: str            # "android" | "ios"
    device: str              # "SM-S918B", "iPhone15,2", ...
    manufacturer: str        # "samsung", "apple", ...
    os_version: str          # "android 14", "ios 18.6", ...

    # === Acquisition ===
    install_date: datetime
    acquisition_source: str  # organic / facebook / google_ads / tiktok
    campaign_id: str | None  # None nếu organic

    # === Segment ===
    player_segment: str      # payer segment
    engagement_segment: str  # casual / regular / engaged / hardcore
    churn_segment: str       # lifecycle: early_churn / normal_retention / high_retention

    # === Progression ===
    current_level: int = 1
    current_world: int = 1
    # attempt_num của level hiện tại (1..MAX_ATTEMPTS_PER_LEVEL)
    # Reset về 1 khi level được Complete
    current_attempt_num: int = 1

    # === Economy (balance hiện tại) ===
    coins: int = 0
    gems: int = 50
    energy: int = 30
    lives: int = 5
    stars: int = 0
    tickets: int = 1

    # === Persistent counter theo lifetime ===
    session_count: int = 0          # tổng số session đã chơi
    transaction_num: int = 0        # counter IAP, không reset theo session
    lifetime_revenue: int = 0       # đơn vị nhỏ nhất (minor unit), USD mặc định

    # === Retention / churn ===
    # Ngày cuối cùng player có session (None nếu chưa chơi)
    last_session_date: datetime | None = None
    churn_status: str = CHURN_STATUS_ACTIVE

    # === Tiện ích nội bộ ===
    # Cờ đánh dấu player vừa install trong simulation (dùng cho Bước 23 retention)
    is_new_install: bool = True

    # === Metadata ===
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def touch(self) -> None:
        """Cập nhật `updated_at` về thời điểm hiện tại – gọi trước khi ghi DB."""
        self.updated_at = datetime.now(tz=timezone.utc)

    def increment_session(self) -> None:
        """Tăng session_count và đánh dấu player đã active."""
        self.session_count += 1
        self.is_new_install = False
        self.touch()

    def increment_transaction(self, amount_minor: int) -> None:
        """Tăng transaction_num và cộng dồn lifetime_revenue.

        Args:
            amount_minor: Số tiền theo đơn vị nhỏ nhất (ví dụ USD -> cents).
                          Phải > 0 theo rule business event.
        """
        if amount_minor <= 0:
            raise ValueError(
                f"amount_minor phải > 0 (transaction_num không tăng cho refund/zero), "
                f"nhận được: {amount_minor}"
            )
        self.transaction_num += 1
        self.lifetime_revenue += amount_minor
        self.touch()

    def is_payer(self) -> bool:
        """Player có khả năng thực hiện IAP không."""
        return self.player_segment != "non_payer"

    def to_dict(self) -> dict[str, Any]:
        """Chuyển sang dict để ghi SQLite / JSON. Datetime -> ISO 8601 string."""
        data = asdict(self)
        # Chuẩn hoá datetime sang ISO 8601 (timezone-aware)
        for key, value in list(data.items()):
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        """Tạo Player từ dict (ngược lại của to_dict)."""
        data = dict(data)  # copy để không mutate input
        for key in ("install_date", "last_session_date", "created_at", "updated_at"):
            if data.get(key) is not None and isinstance(data[key], str):
                data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


# ----------------------------------------------------------------------
# Self-test nhỏ khi chạy trực tiếp file này
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Thêm project root vào sys.path để import config khi chạy độc lập
    import os
    import sys

    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

    from config import game_config, player_config

    # Tạo player mẫu với giá trị hợp lý
    p = Player(
        user_id="player_000001",
        country="VN",
        platform="android",
        device="SM-S918B",
        manufacturer="samsung",
        os_version="android 14",
        install_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        acquisition_source="facebook",
        campaign_id="VN_AUG_01",
        player_segment="mid_payer",
        engagement_segment="engaged",
        churn_segment="normal_retention",
        current_level=5,
        coins=500,
        gems=120,
        energy=25,
    )

    print("Player mẫu vừa tạo:")
    print(f"  user_id       = {p.user_id}")
    print(f"  platform      = {p.platform}")
    print(f"  current_level = {p.current_level}")
    print(f"  coins/gems    = {p.coins}/{p.gems}")
    print(f"  is_payer()    = {p.is_payer()}")

    # Mô phỏng tăng session + transaction
    p.increment_session()
    p.increment_transaction(amount_minor=499)  # GemPack_500 $4.99
    print(f"  Sau session+txn: session_count={p.session_count}, "
          f"transaction_num={p.transaction_num}, "
          f"lifetime_revenue={p.lifetime_revenue}")

    # Round-trip dict
    d = p.to_dict()
    p2 = Player.from_dict(d)
    assert p2.user_id == p.user_id
    assert p2.transaction_num == p.transaction_num
    print(f"  to_dict / from_dict round-trip: OK")

    print(f"\nGame config có: API_VERSION={game_config.API_VERSION}")
    print(f"Player config: tổng weight payer = "
          f"{sum(player_config.PAYER_SEGMENT_WEIGHTS)}")
