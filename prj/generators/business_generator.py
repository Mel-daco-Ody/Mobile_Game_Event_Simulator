"""Business Event Generator – sinh event 'business' cho IAP.

Theo BƯỚC 11 INSTRUCTION.md:

Event business mô phỏng real-money purchase.

Payload mẫu (GameAnalytics schema v2):

    {
        "category": "business",
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

        # Business-specific fields:
        "event_id": "GemPack:GemPack_500",   # itemType:itemId
        "currency": "USD",                    # 3-letter UPPERCASE
        "amount": 499,                        # minor unit
        "transaction_num": 3,                 # lifetime counter
        "cart_type": "menu_shop"              # optional
    }

Rule bắt buộc:
    - event_id format '<product_type>:<product_id>' – KHÔNG chứa
      UUID/timestamp/user_id, KHÔNG tạo dynamic ID.
    - currency: 3-letter UPPERCASE ISO 4217, KHÔNG 'usd', '$', 'dollar'.
    - amount: integer > 0 theo minor unit.
    - transaction_num: lifetime counter từ Player model.
"""

from __future__ import annotations

import re
from typing import Any

from config import monetization_config
from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.monetization import PurchaseDecision


# Pattern ISO 4217 currency code: 3-letter UPPERCASE
_ISO_4217_PATTERN = re.compile(r"^[A-Z]{3}$")


def generate_business_event(
    player: Player,
    session: Session,
    decision: PurchaseDecision,
) -> dict[str, Any]:
    """Sinh event 'business' từ một PurchaseDecision.

    Args:
        player: Player model.
        session: Session model đang chơi.
        decision: PurchaseDecision từ MonetizationSimulator.try_purchase().
                  Chứa client_ts, event_id, amount, currency,
                  transaction_num, cart_type.

    Returns:
        dict event 'business' với đầy đủ common annotations + 4-5 field riêng.

    Raises:
        ValueError: nếu currency không phải 3-letter UPPERCASE,
                    amount <= 0, event_id format sai.
    """
    # Validate currency
    if not _ISO_4217_PATTERN.match(decision.currency):
        raise ValueError(
            f"currency '{decision.currency}' phải là 3-letter UPPERCASE "
            f"(ISO 4217). Ví dụ: USD, EUR, JPY. KHÔNG dùng 'usd', '$', 'dollar'."
        )
    if decision.currency not in monetization_config.VALID_CURRENCIES:
        # Vẫn cho phép ISO hợp lệ nhưng không nằm trong CURRENCY_CONVERSION
        # để tránh block data – chỉ warning, không raise.
        # (Đã raise ở _country_to_currency nếu không có rate.)
        pass

    # Validate amount
    if decision.amount <= 0:
        raise ValueError(
            f"amount phải > 0 (theo minor unit, ví dụ $0.99 -> 99). "
            f"Nhận: {decision.amount}"
        )

    # Validate event_id format (2 phần, KHÔNG chứa UUID/timestamp/user_id)
    if decision.event_id.count(":") != 1:
        raise ValueError(
            f"event_id '{decision.event_id}' phải có format "
            f"'itemType:itemId' (1 dấu ':')."
        )
    for forbidden in (player.user_id,):
        if forbidden and forbidden in decision.event_id:
            raise ValueError(
                f"event_id KHÔNG được chứa user_id '{player.user_id}'"
            )
    uuid_like = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        decision.event_id, flags=re.IGNORECASE
    )
    if uuid_like is not None:
        raise ValueError("event_id KHÔNG được chứa UUID")
    if re.search(r"\d{10,}", decision.event_id):
        raise ValueError("event_id KHÔNG được chứa chuỗi timestamp")

    annotations = create_default_annotations(
        player, session, client_ts=decision.client_ts
    )

    event: dict[str, Any] = {
        **annotations,
        "category": "business",
        # Business-specific fields
        "event_id": decision.event_id,
        "currency": decision.currency,
        "amount": decision.amount,
        "transaction_num": decision.transaction_num,
    }

    # cart_type optional – chỉ thêm nếu có
    if decision.cart_type is not None:
        event["cart_type"] = decision.cart_type

    return event


__all__ = ["generate_business_event"]
