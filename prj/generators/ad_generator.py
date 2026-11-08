"""Ad Event Generator – sinh event 'design' (impression).

Theo BƯỚC 12 INSTRUCTION.md:

Event ad mô phỏng 1 impression (player xem 1 quảng cáo).

Lưu ý: GameAnalytics spec chính thức KHÔNG có ``category: "ad"``.
Category gần nhất là ``impression events`` (xem docs GA). Để tương thích
với sandbox hiện tại, ta map sang ``category: "design"`` với event_id
theo format ``Ad:<ad_type>:<placement>``.

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

        # Ad-as-design-event fields:
        "event_id": "Ad:rewarded:end_of_level",  # Ad:<ad_type>:<placement>
        "value": 1,                              # impression count (>= 0)
        # Optional custom fields (denoted as flat keys):
        # "ad_type": "rewarded",
        # "placement": "end_of_level",
        # "rewarded": true,
        # "coin_reward": 150
    }

Rule bắt buộc:
    - category = "design" (GA không có category "ad" trong spec chính thức).
    - event_id KHÔNG chứa user_id/timestamp/UUID.
    - ad_type ∈ {rewarded, interstitial, banner}.
    - rewarded: bool – true CHỈ khi ad_type='rewarded'.
    - coin_reward: int >= 0 – chỉ có khi rewarded=true.

Liên kết với Economy:
    - Ad 'rewarded' đi kèp với 1 resource event Source:Coins:Ad:Rewarded,
      sinh qua EconomySimulator.award('Coins', 'Ad', 'Rewarded', ...).
"""

from __future__ import annotations

from typing import Any

from gameanalytics.annotations import create_default_annotations
from models import Player, Session
from simulation.economy import (
    EconomySimulator,
    ResourceTransaction,
)
from simulation.ad import AdDecision


# =========================================================================
# Reward helpers – tạo ResourceTransaction liên kết với rewarded ad
# =========================================================================

# Đồng bộ với event_config.RESOURCE_AMOUNT_RANGES["Coins"]["Source:Ad:Rewarded"]
# để amount sinh ra được EconomySimulator hiểu.
REWARDED_SOURCE_ITEMS: dict[str, str] = {
    "currency": "Coins",
    "item_type": "Ad",
    "item_id": "Rewarded",
}


def build_rewarded_resource_tx(
    decision: AdDecision,
    economy: EconomySimulator,
) -> ResourceTransaction:
    """Tạo ResourceTransaction cho coin reward từ ad rewarded.

    Args:
        decision: AdDecision với ad_type='rewarded'.
        economy: EconomySimulator (chỉ dùng để truy cập balance;
                 KHÔNG mutate – caller tự gọi economy.award()).

    Returns:
        ResourceTransaction với event_id = 'Source:Coins:Ad:Rewarded'.
    """
    if not decision.rewarded:
        raise ValueError(
            "build_rewarded_resource_tx() chỉ dùng cho ad rewarded, "
            f"nhận ad_type='{decision.ad_type}'"
        )

    return ResourceTransaction(
        client_ts=decision.client_ts,
        flow_type="Source",
        virtual_currency=REWARDED_SOURCE_ITEMS["currency"],
        item_type=REWARDED_SOURCE_ITEMS["item_type"],
        item_id=REWARDED_SOURCE_ITEMS["item_id"],
        amount=decision.coin_reward,
    )


# =========================================================================
# Ad impression event generator
# =========================================================================

def generate_ad_event(
    player: Player,
    session: Session,
    decision: AdDecision,
) -> dict[str, Any]:
    """Sinh event 'design' (ad impression) từ AdDecision.

    Lưu ý: dùng category='design' thay vì 'ad' vì GameAnalytics spec
    chính thức không có category 'ad'. Event_id vẫn chứa 'Ad:' prefix
    để dễ truy vấn trong dashboard.

    Args:
        player: Player model.
        session: Session model.
        decision: AdDecision từ AdSimulator.

    Returns:
        dict event 'design' với common annotations + ad-specific fields.

    Raises:
        ValueError: nếu ad_type không hợp lệ,
                    rewarded=True nhưng ad_type != 'rewarded',
                    rewarded=False nhưng coin_reward > 0.
    """
    if decision.ad_type not in ("rewarded", "interstitial", "banner"):
        raise ValueError(
            f"ad_type '{decision.ad_type}' không hợp lệ. "
            f"Phải là: rewarded, interstitial, banner."
        )

    # Consistency checks
    if decision.rewarded and decision.ad_type != "rewarded":
        raise ValueError(
            "rewarded=True chỉ hợp lệ khi ad_type='rewarded'. "
            f"Nhận ad_type='{decision.ad_type}'."
        )
    if decision.ad_type == "rewarded" and not decision.rewarded:
        raise ValueError(
            "ad_type='rewarded' nhưng rewarded=False – phải đồng bộ."
        )
    if not decision.rewarded and decision.coin_reward > 0:
        raise ValueError(
            f"Non-rewarded ad mà có coin_reward={decision.coin_reward}."
        )
    if decision.coin_reward < 0:
        raise ValueError(f"coin_reward phải >= 0, nhận {decision.coin_reward}")

    annotations = create_default_annotations(
        player, session, client_ts=decision.client_ts
    )

    # event_id theo format 'Ad:<ad_type>:<placement>' (3 parts, fit GA design spec)
    event_id = f"Ad:{decision.ad_type}:{decision.placement}"

    event: dict[str, Any] = {
        **annotations,
        "category": "design",  # GA không có 'ad' – dùng 'design'
        "event_id": event_id,
        "value": 1,  # 1 impression (GA design event có value optional)
    }

    # Optional metadata fields (flat keys – GA accept extra fields)
    event["ad_type"] = decision.ad_type
    event["placement"] = decision.placement
    event["rewarded"] = decision.rewarded

    # coin_reward chỉ thêm khi rewarded=true
    if decision.rewarded:
        event["coin_reward"] = decision.coin_reward

    return event


__all__ = [
    "generate_ad_event",
    "build_rewarded_resource_tx",
    "REWARDED_SOURCE_ITEMS",
]
