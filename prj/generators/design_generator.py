"""Design Event Generator – sinh event 'design' (gameplay actions).

Theo BƯỚC 13 INSTRUCTION.md:

Design event mô phỏng gameplay actions (kill, upgrade, quest, ...).

Payload mẫu (GameAnalytics schema v2):

    {
        "category": "design",
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

        # Design-specific fields:
        "event_id": "Gameplay:Kill:Boss",   # 1 trong 12 ID cố định
        "value": 738,                       # numeric – dynamic data
        # Optional custom fields (30% probability):
        # "game_mode": "endless",
        # "difficulty": "hard",
        # "ab_group": "B"
    }

Quy tắc KHẮT NGHIỆT:
    - event_id chỉ từ DESIGN_EVENT_IDS cố định (12 ID).
    - KHÔNG chèn user_id/timestamp/UUID/random number vào event_id.
    - KHÔNG tự tạo dynamic event ID.
    - 'value' là numeric (int), KHÔNG phải string hoặc random UUID.
    - Custom fields chỉ từ tập CUSTOM_FIELDS (game_mode, difficulty,
      ab_group), giá trị chỉ từ tập giới hạn của field đó.
"""

from __future__ import annotations

import re
from typing import Any

from config import event_config
from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.design import DesignDecision


def generate_design_event(
    player: Player,
    session: Session,
    decision: DesignDecision,
) -> dict[str, Any]:
    """Sinh event 'design' từ DesignDecision.

    Args:
        player: Player model.
        session: Session model đang chơi.
        decision: DesignDecision từ DesignSimulator.

    Returns:
        dict event 'design' với common annotations + design-specific fields.

    Raises:
        ValueError: nếu event_id không thuộc DESIGN_EVENT_IDS cố định,
                    value không phải int, custom field có key/value ngoài schema,
                    hoặc dynamic ID chứa user_id/UUID/timestamp.
    """
    # --------------------------------------------------------------
    # Validate event_id thuộc tập cố định
    # --------------------------------------------------------------
    if decision.event_id not in event_config.DESIGN_EVENT_IDS:
        raise ValueError(
            f"event_id '{decision.event_id}' KHÔNG thuộc DESIGN_EVENT_IDS "
            f"cố định (12 ID). KHÔNG tạo dynamic event ID."
        )

    # Defensive: event_id KHÔNG chứa user_id/UUID/timestamp
    eid = decision.event_id
    if player.user_id and player.user_id in eid:
        raise ValueError(
            f"event_id '{eid}' KHÔNG được chứa user_id '{player.user_id}'"
        )
    uuid_like = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        eid, flags=re.IGNORECASE,
    )
    if uuid_like is not None:
        raise ValueError(f"event_id '{eid}' KHÔNG được chứa UUID")
    if re.search(r"\d{10,}", eid):
        raise ValueError(f"event_id '{eid}' KHÔNG được chứa timestamp")

    # --------------------------------------------------------------
    # Validate value
    # --------------------------------------------------------------
    if not isinstance(decision.value, int):
        raise ValueError(
            f"value phải là int, nhận {type(decision.value).__name__}"
        )
    if decision.value < 0:
        raise ValueError(f"value phải >= 0, nhận {decision.value}")

    # --------------------------------------------------------------
    # Validate custom_fields (nếu có)
    # --------------------------------------------------------------
    for key, val in decision.custom_fields.items():
        if key not in event_config.CUSTOM_FIELDS:
            raise ValueError(
                f"custom field '{key}' KHÔNG thuộc CUSTOM_FIELDS "
                f"(chỉ cho phép: {list(event_config.CUSTOM_FIELDS.keys())})"
            )
        allowed_values = event_config.CUSTOM_FIELDS[key]
        if val not in allowed_values:
            raise ValueError(
                f"custom field '{key}' = '{val}' KHÔNG thuộc tập "
                f"cho phép {allowed_values}"
            )

    annotations = create_default_annotations(
        player, session, client_ts=decision.client_ts
    )

    event: dict[str, Any] = {
        **annotations,
        "category": "design",
        # Design-specific fields
        "event_id": decision.event_id,
        "value": decision.value,
    }

    # Attach custom fields (KHÔNG attach nếu rỗng)
    event.update(decision.custom_fields)

    return event


__all__ = ["generate_design_event"]
