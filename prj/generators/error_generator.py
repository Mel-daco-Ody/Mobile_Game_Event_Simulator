"""Error Event Generator – sinh event 'error' cho game-side issues.

Theo BƯỚC 14 INSTRUCTION.md:

Error event mô phỏng các vấn đề kỹ thuật trong game (network, save, asset).

Payload mẫu (GameAnalytics schema v2):

    {
        "category": "error",
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

        # Error-specific fields:
        "event_id": "error:network_timeout",   # 'error:<message>'
        "severity": "warning",                  # debug|info|warning|error|critical
        "message": "network_timeout",           # ∈ ERROR_MESSAGES
        "stack_trace": "NetworkManager.cs:142 | ..."  # optional – KHÔNG UUID/user_id
    }

Quy tắc CỰC KỲ QUAN TRỌNG (Bước 14):
    - severity ∈ 5 enum: debug / info / warning / error / critical.
    - message ∈ tập ERROR_MESSAGES cố định (5 message).
    - KHÔNG gắn user_id/timestamp/UUID/random number vào event_id
      (event_id = 'error:<message>' – message là tĩnh).
    - Xác suất cực nhỏ (0.001); cap bởi MAX_ERROR_EVENTS_PER_SESSION.
    - KHÔNG spam error event.
"""

from __future__ import annotations

import re
from typing import Any

from config import event_config
from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.error import ErrorDecision


def generate_error_event(
    player: Player,
    session: Session,
    decision: ErrorDecision,
) -> dict[str, Any]:
    """Sinh event 'error' từ ErrorDecision.

    Args:
        player: Player model.
        session: Session model đang chơi.
        decision: ErrorDecision từ ErrorSimulator.

    Returns:
        dict event 'error' với common annotations + error-specific fields.

    Raises:
        ValueError: nếu severity KHÔNG thuộc 5 enum, hoặc message
                    KHÔNG thuộc ERROR_MESSAGES, hoặc stack_trace chứa
                    UUID/timestamp/user_id.
    """
    # --------------------------------------------------------------
    # Validate severity
    # --------------------------------------------------------------
    if decision.severity not in event_config.ERROR_SEVERITY_LEVELS:
        raise ValueError(
            f"severity '{decision.severity}' KHÔNG hợp lệ. "
            f"Phải là 1 trong: {event_config.ERROR_SEVERITY_LEVELS}"
        )

    # --------------------------------------------------------------
    # Validate message
    # --------------------------------------------------------------
    if decision.message not in event_config.ERROR_MESSAGES:
        raise ValueError(
            f"message '{decision.message}' KHÔNG thuộc ERROR_MESSAGES "
            f"cố định (chỉ cho phép: {event_config.ERROR_MESSAGES})"
        )

    # --------------------------------------------------------------
    # Validate stack_trace (nếu có) KHÔNG chứa UUID/timestamp/user_id
    # --------------------------------------------------------------
    if decision.stack_trace is not None:
        st = decision.stack_trace
        uuid_like = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            st, flags=re.IGNORECASE,
        )
        if uuid_like is not None:
            raise ValueError(
                f"stack_trace KHÔNG được chứa UUID"
            )
        if re.search(r"\d{10,}", st):
            raise ValueError(
                f"stack_trace KHÔNG được chứa timestamp"
            )
        if player.user_id and player.user_id in st:
            raise ValueError(
                f"stack_trace KHÔNG được chứa user_id"
            )

    annotations = create_default_annotations(
        player, session, client_ts=decision.client_ts
    )

    # event_id = 'error:<message>' – message là static, KHÔNG random
    event_id = f"error:{decision.message}"

    event: dict[str, Any] = {
        **annotations,
        "category": "error",
        "event_id": event_id,
        "severity": decision.severity,
        "message": decision.message,
    }

    # stack_trace optional – chỉ thêm nếu có
    if decision.stack_trace is not None:
        event["stack_trace"] = decision.stack_trace

    return event


__all__ = ["generate_error_event"]
