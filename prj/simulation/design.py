"""Design Event Simulator – mô phỏng gameplay actions (kill, upgrade, ...).

Theo BƯỚC 13 INSTRUCTION.md:

Các design event cố định (12 event_id), KHÔNG tạo dynamic ID:
    Gameplay:Kill:Zombie          → damage (10-80)
    Gameplay:Kill:Boss            → damage (100-1000)
    Gameplay:Kill:Tank            → damage (50-300)
    Gameplay:Kill:Ranger          → damage (30-150)
    Gameplay:Weapon:Upgrade       → new_level (1-5)
    Gameplay:Skill:Unlock         → skill_tier (1-3)
    Gameplay:Shop:Open            → items_viewed (1-12)
    Gameplay:Shop:Close           → items_viewed (1-12)
    Gameplay:Quest:Complete       → coin_reward (100-500)
    Gameplay:Achievement:Unlock   → coin_reward (200-1000)
    Gameplay:Chest:Open           → rarity (1-5)
    Gameplay:DailyReward:Claim    → streak_day (1-7)

Quy tắc CỰC KỲ QUAN TRỌNG (Bước 13):
    - KHÔNG đưa user_id/timestamp/UUID/random number vào event hierarchy.
    - KHÔNG tạo quá nhiều unique event ID (đã cố định 12 ID).
    - Dynamic info → dùng field 'value' (numeric) hoặc CUSTOM_FIELDS.
    - CUSTOM_FIELDS là tập giới hạn: game_mode, difficulty, ab_group.
    - Mỗi custom field được attach với xác suất CUSTOM_FIELD_PROBABILITY.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from config import event_config


# =========================================================================
# Design decision (data carrier)
# =========================================================================

@dataclass
class DesignDecision:
    """Quyết định phát sinh 1 design event.

    Attributes:
        client_ts: unix timestamp.
        event_id: 1 trong 12 ID cố định từ DESIGN_EVENT_IDS.
        value: numeric – sample theo DESIGN_VALUE_RANGES[event_id].
        custom_fields: dict các field custom (game_mode, difficulty,
                       ab_group). Có thể rỗng.
    """

    client_ts: int
    event_id: str
    value: int
    custom_fields: dict[str, str] = field(default_factory=dict)


# =========================================================================
# Design Simulator
# =========================================================================

class DesignSimulator:
    """Sinh design event cho player trong 1 session.

    State:
        rng: random.Random instance.
    """

    # Phân bố xác suất pick event_id – uniform ngẫu nhiên trong 12 ID.
    # Ở đây có thể customize nếu muốn gameplay cụ thể (VD: heavy kill thì
    # weight cho Kill events), nhưng mặc định uniform để không lệch test.

    def __init__(self, rng: random.Random | None = None) -> None:
        if rng is None:
            rng = random.Random()
        self._rng = rng

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def try_event(
        self,
        player: Any,
        client_ts: int,
    ) -> DesignDecision | None:
        """Thử phát 1 design event trong session cho player.

        Quyết định:
            1. Roll dice theo DESIGN_EVENT_PROBABILITY_PER_SESSION[engagement].
            2. Nếu fail → None.
            3. Pick event_id (uniform từ 12 ID cố định).
            4. Sample value theo DESIGN_VALUE_RANGES[event_id].
            5. Với mỗi custom field trong CUSTOM_FIELDS, roll dice
               CUSTOM_FIELD_PROBABILITY để attach.

        Args:
            player: Player model (chỉ đọc engagement_segment).
            client_ts: unix timestamp.

        Returns:
            DesignDecision hoặc None.
        """
        segment = player.engagement_segment
        prob = event_config.DESIGN_EVENT_PROBABILITY_PER_SESSION.get(
            segment, 0.5
        )

        if self._rng.random() >= prob:
            return None

        # Pick event_id
        event_id = self._rng.choice(event_config.DESIGN_EVENT_IDS)
        # Sample value
        value = self._sample_value(event_id)
        # Attach custom fields
        custom = self._sample_custom_fields()

        return DesignDecision(
            client_ts=client_ts,
            event_id=event_id,
            value=value,
            custom_fields=custom,
        )

    def try_events_for_session(
        self,
        player: Any,
        client_start_ts: int,
        n_max: int = 5,
        ts_step_seconds: int = 60,
    ) -> list[DesignDecision]:
        """Thử nhiều design event trong 1 session.

        Args:
            player: Player model.
            client_start_ts: timestamp bắt đầu session.
            n_max: giới hạn số event (anti-spam).
            ts_step_seconds: mỗi event cách nhau bao nhiêu giây.

        Returns:
            list DesignDecision (có thể rỗng).
        """
        decisions: list[DesignDecision] = []
        for i in range(n_max):
            ts = client_start_ts + i * ts_step_seconds
            d = self.try_event(player, ts)
            if d is not None:
                decisions.append(d)
        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def pick_event_id(self) -> str:
        """Random 1 event_id từ DESIGN_EVENT_IDS cố định (12 ID)."""
        return self._rng.choice(event_config.DESIGN_EVENT_IDS)

    def _sample_value(self, event_id: str) -> int:
        """Sample numeric value theo DESIGN_VALUE_RANGES.

        Mặc định 1 nếu event_id không có range (defensive).
        """
        if event_id not in event_config.DESIGN_VALUE_RANGES:
            return 1
        lo, hi, _ = event_config.DESIGN_VALUE_RANGES[event_id]
        return self._rng.randint(lo, hi)

    def _sample_custom_fields(self) -> dict[str, str]:
        """Với mỗi field trong CUSTOM_FIELDS, roll dice theo probability.

        Chỉ trả về những field nào "trúng" dice.
        """
        out: dict[str, str] = {}
        prob = event_config.CUSTOM_FIELD_PROBABILITY
        for field_name, options in event_config.CUSTOM_FIELDS.items():
            if self._rng.random() < prob:
                out[field_name] = self._rng.choice(options)
        return out


__all__ = ["DesignSimulator", "DesignDecision"]
