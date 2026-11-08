"""Session Model – định nghĩa một gameplay session.

Theo BƯỚC 5 trong INSTRUCTION.md:

Cấu trúc một session:
    session_start (user event)
      → gameplay
      → progression (Start/Fail/Complete)
      → ads (impression + rewarded)
      → resources (Source/Sink)
      → purchase (business event, optional)
      → session_end

Đặc điểm:
    - session_id: UUID v4, mỗi session một UUID riêng, KHÔNG dùng chung cho cả player.
    - session_num: bắt đầu từ 1, tăng dần theo lifetime của installation.
    - duration: KHÔNG random đều 0→172800 mà dùng distribution theo engagement segment:
        Casual   : 1–10 phút   (60–600 giây)
        Regular  : 10–30 phút  (600–1800 giây)
        Engaged  : 20–60 phút  (1200–3600 giây)
        Hardcore : 30–120 phút (1800–7200 giây)

Module cung cấp:
    - Session: dataclass định nghĩa cấu trúc session
    - generate_session: sinh một session mới cho player
    - SESSION_DURATION_SECONDS: dict engagement → (min_sec, max_sec),
        mirror của game_config.SESSION_DURATION_RANGE_SECONDS.
        Consumer có thể import từ đây hoặc từ game_config đều OK.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import game_config


# =========================================================================
# Duration range theo engagement segment.
#
# Lưu ý: KHÔNG hard-code ở đây – lấy từ game_config.SESSION_DURATION_RANGE_SECONDS
# để đảm bảo tính đồng bộ với Bước 1 ("không hard-code").
# SESSION_DURATION_SECONDS giữ lại là một alias cho consumer tiện import.
# =========================================================================
SESSION_DURATION_SECONDS: dict[str, tuple[int, int]] = dict(
    game_config.SESSION_DURATION_RANGE_SECONDS
)


@dataclass
class Session:
    """Một gameplay session của player.

    Attributes:
        session_id: UUID v4, unique cho mỗi session.
        user_id: user_id của player sở hữu session này.
        session_num: số thứ tự session (1, 2, 3...), bắt đầu từ 1,
            tăng dần theo lifetime của installation.
        session_start_ts: unix timestamp (giây) khi session bắt đầu.
        session_end_ts: unix timestamp (giây) khi session kết thúc.
            None nếu session chưa kết thúc.
        duration_seconds: thời lượng session (session_end_ts - session_start_ts).
            = 0 nếu session chưa kết thúc.
        connection_type: 'wifi' hoặc 'wwan' (mobile data).
        engagement_segment: engagement segment tại thời điểm session.
            Snapshot từ player model (không thay đổi giữa session).
        platform: 'android' hoặc 'ios', snapshot từ player model.
    """

    user_id: str
    session_num: int  # bắt đầu từ 1
    session_start_ts: int  # unix seconds
    connection_type: str  # 'wifi' | 'wwan'
    engagement_segment: str  # snapshot
    platform: str  # snapshot
    session_id: str = field(default="")
    session_end_ts: int | None = None
    duration_seconds: int = 0

    def __post_init__(self) -> None:
        """Sinh UUID v4 nếu chưa có; tính duration nếu đã có end_ts."""
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
        if self.session_end_ts is not None:
            self.duration_seconds = max(0, self.session_end_ts - self.session_start_ts)

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """Session đang mở (chưa gọi close())."""
        return self.session_end_ts is None

    @property
    def start_datetime(self) -> datetime:
        """Convert session_start_ts → datetime (timezone-aware UTC)."""
        return datetime.fromtimestamp(self.session_start_ts, tz=timezone.utc)

    @property
    def end_datetime(self) -> datetime | None:
        """Convert session_end_ts → datetime (timezone-aware UTC)."""
        if self.session_end_ts is None:
            return None
        return datetime.fromtimestamp(self.session_end_ts, tz=timezone.utc)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def close(self, end_ts: int | None = None) -> "Session":
        """Đóng session, ghi session_end_ts và tính duration_seconds.

        Args:
            end_ts: unix timestamp kết thúc. Mặc định = now (UTC).
                   Nếu truyền < session_start_ts thì raise ValueError.

        Returns:
            Session đã được cập nhật (self), để tiện chaining.
        """
        if end_ts is None:
            end_ts = int(datetime.now(tz=timezone.utc).timestamp())
        if end_ts < self.session_start_ts:
            raise ValueError(
                f"session_end_ts ({end_ts}) không thể nhỏ hơn "
                f"session_start_ts ({self.session_start_ts})"
            )
        self.session_end_ts = end_ts
        self.duration_seconds = end_ts - self.session_start_ts
        return self

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Convert thành dict (dùng cho lưu DB hoặc serialize)."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "session_num": self.session_num,
            "session_start_ts": self.session_start_ts,
            "session_end_ts": self.session_end_ts,
            "duration_seconds": self.duration_seconds,
            "connection_type": self.connection_type,
            "engagement_segment": self.engagement_segment,
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Tạo Session từ dict."""
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            session_num=data["session_num"],
            session_start_ts=data["session_start_ts"],
            session_end_ts=data.get("session_end_ts"),
            duration_seconds=data.get("duration_seconds", 0),
            connection_type=data["connection_type"],
            engagement_segment=data["engagement_segment"],
            platform=data["platform"],
        )


# =========================================================================
# Session Generator
# =========================================================================

def _pick_connection_type(rng: random.Random) -> str:
    """Chọn connection_type theo weights từ game_config."""
    return rng.choices(
        game_config.CONNECTION_TYPES,
        weights=game_config.CONNECTION_TYPE_WEIGHTS,
        k=1,
    )[0]


def generate_session(
    player: Any,  # Player model
    session_start_ts: int | None = None,
    rng: random.Random | None = None,
) -> Session:
    """Sinh một session mới cho player.

    Args:
        player: Player model (cần field user_id, session_count,
                engagement_segment, platform).
        session_start_ts: unix timestamp bắt đầu session.
            Mặc định = now (UTC).
        rng: instance random.Random để tái lập kết quả.
            Mặc định tạo mới với seed ngẫu nhiên.

    Returns:
        Session mới, đã có session_id (UUID v4), session_num đúng,
        connection_type và engagement_segment snapshot từ player.

    Quy tắc:
        - session_num = player.session_count + 1 (session tiếp theo)
        - duration KHÔNG set ở đây; được tính khi session kết thúc
          (close()).
        - Mỗi lần gọi tạo một UUID mới, không trùng với session nào khác.
    """
    if rng is None:
        rng = random.Random()

    if session_start_ts is None:
        session_start_ts = int(datetime.now(tz=timezone.utc).timestamp())

    # session_num tiếp theo dựa trên số session đã chơi
    session_num = player.session_count + 1

    connection_type = _pick_connection_type(rng)

    return Session(
        session_id="",  # __post_init__ sẽ sinh UUID v4
        user_id=player.user_id,
        session_num=session_num,
        session_start_ts=session_start_ts,
        session_end_ts=None,
        duration_seconds=0,
        connection_type=connection_type,
        engagement_segment=player.engagement_segment,
        platform=player.platform,
    )
