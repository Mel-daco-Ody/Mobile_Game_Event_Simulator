"""User Event Generator – sinh event 'user' (SESSION START).

Theo BƯỚC 7 INSTRUCTION.md:

User event đại diện cho SESSION START – event đầu tiên trong batch đầu
tiên của mỗi session và xuất hiện mỗi khi một session mới bắt đầu.

Payload (đã đầy đủ common annotations):

    {
        "category": "user",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600,          -- unix timestamp
        "sdk_version": "rest api v2",
        "os_version": "android 14",
        "manufacturer": "samsung",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-...",     -- UUID v4
        "session_num": 1
    }

Lưu ý:
    - User event KHÔNG có field riêng ngoài các common annotations.
    - Session start timestamp = session.session_start_ts.
    - Event này là event MỞ ĐẦU mỗi session; sinh ngay khi session mở.
"""

from __future__ import annotations

from typing import Any

from gameanalytics.annotations import create_default_annotations
from models import Player, Session


def generate_user_event(
    player: Player,
    session: Session,
    client_ts: int | None = None,
) -> dict[str, Any]:
    """Sinh event 'user' (session start) cho player & session đã cho.

    Args:
        player: Player model (đã có user_id, os_version, manufacturer, ...).
        session: Session model (đã có session_id, session_num).
        client_ts: unix timestamp override.
                   Mặc định = session.session_start_ts.

    Returns:
        dict event với đầy đủ common annotations + category='user'.

    Ví dụ:
        >>> player, session = load_or_create(...)
        >>> event = generate_user_event(player, session)
        >>> send_event(event)
    """
    if client_ts is None:
        client_ts = session.session_start_ts

    annotations = create_default_annotations(player, session, client_ts=client_ts)

    # User event = common annotations + category
    # KHÔNG có field riêng ngoài category
    event: dict[str, Any] = {
        **annotations,
        "category": "user",
    }

    return event


__all__ = ["generate_user_event"]
