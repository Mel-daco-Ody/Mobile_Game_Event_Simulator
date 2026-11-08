"""GameAnalytics annotations – common fields cho mọi event.

Theo BƯỚC 6 trong INSTRUCTION.md:

Mỗi event được gửi lên GameAnalytics đều phải chứa các field bắt buộc
(chung cho mọi category). Hàm ``create_default_annotations()`` tạo ra
dict chứa các field này; mỗi event generator chỉ cần thêm
``category`` + các field riêng của event đó.

Field bắt buộc (schema v2):
    {
        "v": 2,
        "user_id": "...",        -- player.user_id
        "client_ts": ...,        -- unix ts đã cộng offset server
        "sdk_version": "rest api v2",   -- BẮT BUỘC, KHÔNG đổi
        "os_version": "...",     -- player.os_version
        "manufacturer": "...",   -- player.manufacturer
        "device": "...",         -- player.device
        "platform": "...",       -- player.platform
        "session_id": "...",     -- session.session_id (UUID v4)
        "session_num": ...,      -- session.session_num (int >= 1)
        "build": "1.0.0"        -- optional
    }

Lưu ý quan trọng:
    - ``sdk_version`` PHẢI là ``"rest api v2"``, không được tự đặt.
    - ``client_ts`` là Unix timestamp (giây), không phải ISO string.
    - ``build`` tuỳ chọn; dùng GAME_BUILD từ game_config.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from config import game_config
from models import Player, Session


# =========================================================================
# Server timestamp offset
# =========================================================================
# Simulate độ trễ giữa client gửi event và server nhận event.
# Trong thực tế, offset này có thể âm hoặc dương tuỳ network latency.
# Giá trị mặc định = 0 (event gửi ngay lập tức).
_SERVER_TS_OFFSET_SECONDS: int = 0


def set_server_ts_offset(seconds: int) -> None:
    """Thay đổi server timestamp offset (dùng trong test hoặc simulation).

    Args:
        seconds: offset cộng thêm vào client_ts.
                > 0: server nhận muộn hơn client (simulate delay)
                < 0: server nhận sớm hơn client
                = 0: không offset (default)
    """
    global _SERVER_TS_OFFSET_SECONDS
    _SERVER_TS_OFFSET_SECONDS = seconds


def get_server_ts_offset() -> int:
    """Trả về server timestamp offset hiện tại."""
    return _SERVER_TS_OFFSET_SECONDS


def _current_ts() -> int:
    """Trả về unix timestamp hiện tại (UTC, giây)."""
    return int(datetime.now(tz=timezone.utc).timestamp())


# =========================================================================
# Annotation factory
# =========================================================================

def create_default_annotations(
    player: Player,
    session: Session,
    client_ts: int | None = None,
) -> dict[str, Any]:
    """Tạo dict chứa common fields cho mọi GameAnalytics event.

    Args:
        player: Player model (cần user_id, os_version, manufacturer,
                device, platform).
        session: Session model (cần session_id, session_num).
        client_ts: unix timestamp gốc của event.
                   Mặc định = now (UTC).
                   Sau khi truyền qua ``create_default_annotations``,
                   giá trị này sẽ được cộng thêm ``_SERVER_TS_OFFSET_SECONDS``.

    Returns:
        Dict với các field bắt buộc theo GameAnalytics schema v2.
        Event generator gọi hàm này rồi thêm ``category`` + fields riêng.

    Ví dụ:
        >>> annotations = create_default_annotations(player, session)
        >>> event = {**annotations, "category": "progression", "event_id": "level_5"}
    """
    if client_ts is None:
        client_ts = _current_ts()

    return {
        # Schema version (bắt buộc)
        "v": game_config.EVENT_VERSION,  # 2

        # User & session identification
        "user_id": player.user_id,
        "session_id": session.session_id,  # UUID v4 string
        "session_num": session.session_num,  # int >= 1

        # Timestamp (đã cộng server offset)
        "client_ts": client_ts + _SERVER_TS_OFFSET_SECONDS,

        # SDK version – BẮT BUỘC phải là "rest api v2"
        # KHÔNG được đặt thành "Python 1.0" hay "Simulator 1.0"
        "sdk_version": game_config.SDK_VERSION,  # "rest api v2"

        # Device info (snapshot từ player, không thay đổi giữa session)
        "os_version": player.os_version,
        "manufacturer": player.manufacturer,
        "device": player.device,
        "platform": player.platform,

        # Build version (optional)
        "build": game_config.GAME_BUILD,  # "1.0.0"
    }
