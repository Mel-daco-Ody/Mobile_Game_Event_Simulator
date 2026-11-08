"""Session End Event Generator – sinh event 'session_end' ở cuối session.

Theo BƯỚC 8 INSTRUCTION.md:

Required field: ``length`` (integer, ``0 <= length <= 172800`` giây).

Rule bắt buộc:

    length = session_end.client_ts - session_start.client_ts

Payload mẫu:

    {
        "category": "session_end",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759306442,
        "sdk_version": "rest api v2",
        "os_version": "android 14",
        "manufacturer": "samsung",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-...",
        "session_num": 1,
        "length": 842
    }

Lưu ý:
    - length là field DUY NHẤT riêng của session_end (ngoài common annotations).
    - GameAnalytics giới hạn: 0 <= length <= 172800 (48 giờ).
    - Session PHẢI được close() trước khi gọi generate_session_end.
"""

from __future__ import annotations

from typing import Any

from config import game_config
from gameanalytics.annotations import create_default_annotations
from models import Player, Session


def generate_session_end_event(
    player: Player,
    session: Session,
    client_ts: int | None = None,
) -> dict[str, Any]:
    """Sinh event 'session_end' cho session đã kết thúc.

    Args:
        player: Player model.
        session: Session model đã được close() (có session_end_ts).
        client_ts: unix timestamp của thời điểm gửi event.
                   Mặc định = session.session_end_ts.

    Returns:
        dict event với đầy đủ common annotations + category + length.

    Raises:
        ValueError: nếu session chưa được close().
                    nếu length > SESSION_MAX_LENGTH_SECONDS.
                    nếu length < 0.
    """
    if session.is_open:
        raise ValueError(
            f"Session {session.session_id} chưa được close() – không thể sinh "
            f"session_end event. Hãy gọi session.close() trước."
        )

    if client_ts is None:
        client_ts = session.session_end_ts

    annotations = create_default_annotations(player, session, client_ts=client_ts)

    # length là field DUY NHẤT riêng của session_end
    length = session.duration_seconds

    # Validate theo GameAnalytics rule
    if length < 0:
        raise ValueError(
            f"length ({length}) không hợp lệ – âm. "
            f"session_start_ts={session.session_start_ts}, "
            f"session_end_ts={session.session_end_ts}"
        )
    if length > game_config.SESSION_MAX_LENGTH_SECONDS:
        raise ValueError(
            f"length ({length}s = {length / 3600:.2f}h) vượt quá giới hạn "
            f"GameAnalytics {game_config.SESSION_MAX_LENGTH_SECONDS}s "
            f"(48 giờ)."
        )

    event: dict[str, Any] = {
        **annotations,
        "category": "session_end",
        "length": length,
    }

    return event


__all__ = ["generate_session_end_event"]
