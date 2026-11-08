"""Ad Simulator – mô phỏng Rewarded / Interstitial / Banner ad.

Theo BƯỚC 12 INSTRUCTION.md:

Mô phỏng 3 loại ad:
    rewarded     : player xem video, nhận coin reward
    interstitial : full-screen ad giữa session, không có reward
    banner       : ad nhỏ thường trực, không có reward

Ad flow (rewarded):
    Session
      ↓
    Ad Impression
      ↓
    Rewarded Ad
      ↓
    Player receives reward
      ↓
    Resource Source:Coins:Ad:Rewarded

Quy tắc:
    - Số ad trong session phụ thuộc engagement (casual ít, hardcore nhiều).
    - Mỗi ad 'rewarded' phải sinh CẶP (impression, resource source)
      để liên kết Ad Engagement ↔ Economy ↔ Monetization.
    - Interstitial / Banner chỉ sinh 1 impression event, không có resource.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from config import event_config, monetization_config


# =========================================================================
# Ad Decision (data carrier – generator chuyển thành event)
# =========================================================================

@dataclass
class AdDecision:
    """Quyết định phát sinh một ad.

    Attributes:
        client_ts: unix timestamp của impression.
        ad_type: 'rewarded' | 'interstitial' | 'banner'.
        ad_event_id: event_id cho ad event (đã được simulator build sẵn
                     theo schema 'ad:<ad_type>' – KHÔNG tự ý thêm field).
        placement: nơi phát ad ('end_of_level' | 'menu_shop' | 'gameplay' | ...).
                   Phục vụ taxonomy.
        rewarded: True nếu ad_type='rewarded'. False cho 2 loại kia.
        coin_reward: số coin thưởng nếu rewarded (>= 0). Đã sample.
                     Mặc định 0 cho non-rewarded.
        eCPM_estimated: USD eCPM ước tính (eCPM_BY_AD_TYPE × country factor).
                        Phục vụ revenue attribution, KHÔNG ghi vào ad event
                        payload (giữ event GA schema clean).
    """

    client_ts: int
    ad_type: str
    ad_event_id: str
    placement: str
    rewarded: bool
    coin_reward: int = 0
    eCPM_estimated: float = 0.0


# =========================================================================
# Default placements (taxonomy)
# =========================================================================
AD_PLACEMENTS: list[str] = [
    "end_of_level",
    "menu_shop",
    "gameplay_idle",
    "revive_attempt",
    "out_of_energy",
]


# =========================================================================
# Ad Simulator
# =========================================================================

class AdSimulator:
    """Mô phỏng phát ad cho player trong một session.

    State nội bộ:
        rng: random.Random instance.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        if rng is None:
            rng = random.Random()
        self._rng = rng

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def try_session_ad(
        self,
        player: Any,
        client_ts: int,
        placement: str | None = None,
    ) -> AdDecision | None:
        """Phát 1 ad trong session (nếu roll dice thành công).

        Quyết định:
            1. Lấy AD_PROBABILITY_PER_SESSION[engagement_segment] → roll dice.
            2. Nếu fail → return None (không phát ad).
            3. Chọn ad_type theo AD_TYPE_WEIGHTS.
            4. Nếu rewarded → sample coin_reward từ REWARDED_AD_COIN_REWARD_RANGE.
            5. Tính eCPM ước tính theo ad_type + country.

        Args:
            player: Player model (chỉ đọc engagement_segment, country).
            client_ts: unix timestamp.
            placement: nếu None → random từ AD_PLACEMENTS.

        Returns:
            AdDecision (nếu có ad), hoặc None nếu không phát.
        """
        segment = player.engagement_segment
        prob = event_config.AD_PROBABILITY_PER_SESSION.get(segment, 0.5)

        # Roll dice: có phát ad trong session này hay không.
        if self._rng.random() >= prob:
            return None

        # Chọn ad type
        ad_type = self._rng.choices(
            event_config.AD_TYPES,
            weights=event_config.AD_TYPE_WEIGHTS,
            k=1,
        )[0]

        # Sample coin reward cho rewarded
        is_rewarded = ad_type == "rewarded"
        coin_reward = 0
        if is_rewarded:
            lo, hi = event_config.REWARDED_AD_COIN_REWARD_RANGE
            coin_reward = self._rng.randint(lo, hi)

        # Placements
        if placement is None:
            placement = self._rng.choice(AD_PLACEMENTS)

        # eCPM ước tính
        ecpm_by_type = event_config.ECPM_BY_AD_TYPE[ad_type]
        ecpm_by_country = monetization_config.ECPM_BY_COUNTRY.get(
            player.country, ecpm_by_type
        )
        # Combine: weighted (type là chính, country là modifier)
        eCPM_estimated = (ecpm_by_type + ecpm_by_country) / 2.0

        return AdDecision(
            client_ts=client_ts,
            ad_type=ad_type,
            ad_event_id=f"ad:{ad_type}",
            placement=placement,
            rewarded=is_rewarded,
            coin_reward=coin_reward,
            eCPM_estimated=eCPM_estimated,
        )

    def try_session_ads(
        self,
        player: Any,
        client_start_ts: int,
        n_max: int = 3,
    ) -> list[AdDecision]:
        """Thử phát nhiều ad trong 1 session, trả list các AdDecision.

        Số lượng ad giới hạn bởi n_max để tránh spam.
        Mỗi ad phải cách nhau một khoảng thời gian tối thiểu (60s).

        Args:
            player: Player model.
            client_start_ts: unix timestamp session start.
            n_max: số ad tối đa trong 1 session.

        Returns:
            list[AdDecision] – có thể rỗng.
        """
        decisions: list[AdDecision] = []
        last_ts = client_start_ts
        # Mỗi ad cách nhau >= 60s
        for i in range(n_max):
            ts = client_start_ts + i * 90  # cách 90s
            d = self.try_session_ad(player, ts)
            if d is not None:
                decisions.append(d)
                last_ts = d.client_ts
        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def compute_ecpm(ad_type: str, country: str) -> float:
        """Compute expected eCPM (USD) cho 1 ad_type ở country.

        Trả về trung bình giữa ECPM_BY_AD_TYPE và ECPM_BY_COUNTRY.
        """
        ecpm_type = event_config.ECPM_BY_AD_TYPE.get(ad_type, 1.0)
        ecpm_country = monetization_config.ECPM_BY_COUNTRY.get(country, 1.0)
        return (ecpm_type + ecpm_country) / 2.0

    @staticmethod
    def estimated_revenue_usd(impressions: list[AdDecision]) -> float:
        """Tổng revenue ước tính USD = Σ eCPM/1000 cho mỗi impression."""
        return sum(d.eCPM_estimated for d in impressions) / 1000.0


__all__ = ["AdSimulator", "AdDecision", "AD_PLACEMENTS"]
