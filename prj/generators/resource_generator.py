"""Resource Event Generator – sinh event 'resource' (Source/Sink).

Theo BƯỚC 10 INSTRUCTION.md:

Event resource mô hình hoá virtual economy.

Payload mẫu (GameAnalytics schema v2):

    {
        "category": "resource",
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

        # Resource-specific fields:
        "event_id": "Source:Coins:Gameplay:LevelReward",
        "flow_type": "Source",            # hoặc "Sink"
        "virtual_currency": "Coins",      # Coins/Gems/Energy/Lives/Stars/Tickets
        "item_type": "Gameplay",          # Gameplay/Shop/Ad/Upgrade/...
        "item_id": "LevelReward",         # LevelReward/HealthPotion/...
        "amount": 500                     # signed: +Source, -Sink
    }

Rule bắt buộc:
    - event_id format: ``<flow>:<currency>:<item_type>:<item_id>``.
    - KHÔNG chứa UUID/timestamp/user_id trong event_id.
    - amount: Source > 0, Sink < 0 (đã có dấu).
    - Quy tắc chống spam: 1 transaction = 1 event (không tạo N event +1).
"""

from __future__ import annotations

from typing import Any

from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.economy import ResourceTransaction


def generate_resource_event(
    player: Player,
    session: Session,
    transaction: ResourceTransaction,
) -> dict[str, Any]:
    """Sinh event 'resource' từ một ResourceTransaction.

    Args:
        player: Player model.
        session: Session model đang chơi.
        transaction: ResourceTransaction từ EconomySimulator.award() hoặc .spend().
                     Chứa client_ts, flow_type, virtual_currency, item_type,
                     item_id, amount (đã có dấu).

    Returns:
        dict event 'resource' với đầy đủ common annotations + 5 field riêng.

    Raises:
        ValueError: nếu flow_type không phải Source/Sink,
                    hoặc amount không đúng dấu (Source <= 0 hoặc Sink >= 0).
    """
    if transaction.flow_type not in ("Source", "Sink"):
        raise ValueError(
            f"flow_type '{transaction.flow_type}' không hợp lệ. "
            f"Phải là 'Source' hoặc 'Sink'."
        )
    if transaction.flow_type == "Source" and transaction.amount <= 0:
        raise ValueError(
            f"Source amount phải > 0, nhận {transaction.amount}"
        )
    if transaction.flow_type == "Sink" and transaction.amount >= 0:
        raise ValueError(
            f"Sink amount phải < 0, nhận {transaction.amount}"
        )

    annotations = create_default_annotations(
        player, session, client_ts=transaction.client_ts
    )

    event: dict[str, Any] = {
        **annotations,
        "category": "resource",
        # Resource-specific fields
        "event_id": transaction.event_id,
        "flow_type": transaction.flow_type,
        "virtual_currency": transaction.virtual_currency,
        "item_type": transaction.item_type,
        "item_id": transaction.item_id,
        "amount": transaction.amount,  # signed
    }

    return event


__all__ = ["generate_resource_event"]
