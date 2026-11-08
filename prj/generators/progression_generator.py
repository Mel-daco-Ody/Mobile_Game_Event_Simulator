"""Progression Event Generator – sinh event 'progression' (Start/Fail/Complete).

Theo BƯỚC 9 INSTRUCTION.md:

Event progression mô phỏng Level/Stage/Chapter/Quest với 3 trạng thái
``Start``, ``Fail``, ``Complete``.

Payload mẫu (GameAnalytics schema v2 – theo docs chính thức):

    {
        "category": "progression",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600,
        "sdk_version": "rest api v2",
        "os_version": "android 14",
        "manufacturer": "samsung",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-...",
        "session_num": 1,
        "build": "1.0.0",

        # Progression-specific fields (theo GA spec):
        "event_id": "Start:World1:Level05",   # Status:World<n>:Level<nn>
        "attempt_num": 1                      # int >= 0, optional
        # KHÔNG gửi start_system, end_system, complete_status (lấy từ event_id)
    }

Rule bắt buộc (theo GameAnalytics spec):
    - event_id format: ``<Start|Fail|Complete>:<progression1>[:<progression2>[:<progression3>]]``
    - KHÔNG chứa UUID / timestamp / user_id.
    - attempt_num: int >= 0, optional (chỉ gửi khi > 0).
"""

from __future__ import annotations

from typing import Any

from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.progression import (
    COMPLETE,
    FAIL,
    START,
    ProgressionAction,
)


def generate_progression_event(
    player: Player,
    session: Session,
    action: ProgressionAction,
) -> dict[str, Any]:
    """Sinh event 'progression' từ một ProgressionAction.

    Args:
        player: Player model.
        session: Session model đang chơi.
        action: ProgressionAction từ ProgressionSimulator.next_action()
                (chứa client_ts, event_id, complete_status, attempt_num,
                 start_system, end_system).

    Returns:
        dict event 'progression' với đầy đủ common annotations + fields
        theo GameAnalytics spec (chỉ event_id + attempt_num optional).

    Raises:
        ValueError: nếu complete_status không phải Start/Fail/Complete.
    """
    if action.complete_status not in (START, FAIL, COMPLETE):
        raise ValueError(
            f"complete_status không hợp lệ: '{action.complete_status}'. "
            f"Phải là một trong {{'{START}', '{FAIL}', '{COMPLETE}'}}."
        )

    annotations = create_default_annotations(
        player, session, client_ts=action.client_ts
    )

    # GA spec chỉ yêu cầu event_id; attempt_num optional.
    # KHÔNG gửi start_system, end_system, complete_status (lấy từ event_id).
    event: dict[str, Any] = {
        **annotations,
        "category": "progression",
        "event_id": action.event_id,           # KHÔNG chứa UUID/timestamp/user_id
    }

    # attempt_num chỉ thêm khi > 0 (GA: optional, int >= 0)
    if action.attempt_num > 0:
        event["attempt_num"] = action.attempt_num

    return event


__all__ = ["generate_progression_event"]
