"""Economy Simulator – quản lý balance & quyết định Source/Sink logic.

Theo BƯỚC 10 INSTRUCTION.md:

Mô hình hoá virtual economy cho 6 currencies:
    Coins, Gems, Energy, Lives, Stars, Tickets

Event ID format:
    [flowType]:[virtualCurrency]:[itemType]:[itemId]
    ví dụ: "Source:Coins:Gameplay:LevelReward"

Quy tắc chống spam:
    - Nhận +500 Coins → chỉ tạo MỘT event với amount=500, KHÔNG tạo 500 event +1.
    - Đây là trách nhiệm của Simulator: chỉ return 1 transaction cho mỗi Source/Sink.

Module này cung cấp:
    - ResourceTransaction: dataclass cho 1 giao dịch.
    - EconomySimulator: quản lý balance + chọn item + sample amount.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from config import event_config


# =========================================================================
# Resource Transaction
# =========================================================================

# Field name theo GameAnalytics schema v2 cho resource events
CURRENCY_FIELD: str = "virtual_currency"  # tên field theo GA schema


@dataclass
class ResourceTransaction:
    """Một giao dịch Source/Sink cần phát 1 event.

    Attributes:
        client_ts: unix timestamp gửi event.
        flow_type: "Source" hoặc "Sink".
        virtual_currency: tên currency ("Coins", "Gems", ...).
        item_type: loại item ("Gameplay", "Shop", "Ad", ...).
        item_id: id của item ("LevelReward", "HealthPotion", ...).
        amount: số nguyên > 0 cho Source, < 0 cho Sink (đã có dấu).
    """

    client_ts: int
    flow_type: str
    virtual_currency: str
    item_type: str
    item_id: str
    amount: int  # signed: +Source, -Sink

    @property
    def event_id(self) -> str:
        """Build event_id theo INSTRUCTION format.

        Format: ``[flowType]:[virtualCurrency]:[itemType]:[itemId]``
        Ví dụ: ``Source:Coins:Gameplay:LevelReward``
        KHÔNG chứa UUID/timestamp/user_id.
        """
        return (
            f"{self.flow_type}:{self.virtual_currency}:"
            f"{self.item_type}:{self.item_id}"
        )


# =========================================================================
# Economy Simulator
# =========================================================================

class EconomySimulator:
    """Quản lý economy cho 1 player xuyên suốt simulation.

    State (balance) lưu trực tiếp trên Player model:
        player.coins, player.gems, player.energy, player.lives,
        player.stars, player.tickets.

    Instance state:
        self._rng: random.Random instance.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        if rng is None:
            rng = random.Random()
        self._rng = rng

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_currency(currency: str) -> None:
        if currency not in event_config.RESOURCE_CURRENCIES:
            raise ValueError(
                f"virtual_currency '{currency}' không hợp lệ. "
                f"Phải là một trong {event_config.RESOURCE_CURRENCIES}."
            )

    @staticmethod
    def _validate_flow(flow_type: str) -> None:
        if flow_type not in event_config.RESOURCE_FLOW_TYPES:
            raise ValueError(
                f"flow_type '{flow_type}' không hợp lệ. "
                f"Phải là một trong {event_config.RESOURCE_FLOW_TYPES}."
            )

    @staticmethod
    def _balance_attr(currency: str) -> str:
        """Map currency -> tên field trên Player model.

        Player model dùng lowercase: coins, gems, energy, lives, stars, tickets.
        """
        return currency.lower()

    def _get_balance(self, player: Any, currency: str) -> int:
        return getattr(player, self._balance_attr(currency))

    def _set_balance(self, player: Any, currency: str, value: int) -> None:
        setattr(player, self._balance_attr(currency), value)

    def _pick_item(self, currency: str, flow_type: str) -> tuple[str, str]:
        items = event_config.RESOURCE_ITEMS[currency][flow_type.lower()]
        return self._rng.choice(items)

    def _sample_amount(self, currency: str, flow_type: str,
                       item_type: str, item_id: str) -> int:
        lo, hi = event_config.resource_amount_range(
            currency, flow_type, item_type, item_id
        )
        # integer uniform trong [lo, hi]
        return self._rng.randint(lo, hi)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def award(self, player: Any, currency: str, item_type: str, item_id: str,
              client_ts: int, amount: int | None = None) -> ResourceTransaction:
        """Source: cộng currency cho player, trả về 1 transaction.

        Args:
            player: Player model (sẽ bị mutate balance).
            currency: virtual_currency ("Coins", "Gems", ...).
            item_type: loại source ("Gameplay", "Ad", ...).
            item_id: id của source ("LevelReward", "Rewarded", ...).
            client_ts: unix timestamp.
            amount: nếu None -> sample từ RESOURCE_AMOUNT_RANGES.
                    Nếu truyền thì dùng số đó (dương).

        Returns:
            ResourceTransaction với amount > 0 đã ký.

        Raises:
            ValueError: currency/flow không hợp lệ, amount <= 0.
        """
        self._validate_currency(currency)
        self._validate_flow("Source")

        if amount is None:
            amount = self._sample_amount(currency, "Source", item_type, item_id)
        if amount <= 0:
            raise ValueError(f"Source amount phải > 0, nhận {amount}")

        new_balance = self._get_balance(player, currency) + amount
        self._set_balance(player, currency, new_balance)
        player.touch()

        return ResourceTransaction(
            client_ts=client_ts,
            flow_type="Source",
            virtual_currency=currency,
            item_type=item_type,
            item_id=item_id,
            amount=amount,
        )

    def spend(self, player: Any, currency: str, item_type: str, item_id: str,
              client_ts: int, amount: int | None = None,
              allow_overdraft: bool = False) -> ResourceTransaction | None:
        """Sink: trừ currency cho player, trả về 1 transaction.

        Nếu player không đủ balance và allow_overdraft=False:
            → trả về None, KHÔNG thực hiện giao dịch, KHÔNG phát event.
        Đây là cách anti-spam: không spam event Sink thất bại.

        Args:
            player: Player model.
            currency: virtual_currency.
            item_type: loại sink ("Shop", "Revive", ...).
            item_id: id của sink ("HealthPotion", "Level", ...).
            client_ts: unix timestamp.
            amount: nếu None -> sample từ RESOURCE_AMOUNT_RANGES.
            allow_overdraft: nếu True, vẫn cho phép trừ kể cả khi
                              balance không đủ (kết quả âm).

        Returns:
            ResourceTransaction với amount < 0 đã ký, hoặc None nếu
            player không đủ balance.
        """
        self._validate_currency(currency)
        self._validate_flow("Sink")

        if amount is None:
            amount = self._sample_amount(currency, "Sink", item_type, item_id)
        if amount <= 0:
            raise ValueError(f"Sink magnitude phải > 0, nhận {amount}")

        current = self._get_balance(player, currency)
        if not allow_overdraft and current < amount:
            # Không đủ balance -> bỏ qua, KHÔNG spam event
            return None

        new_balance = current - amount
        self._set_balance(player, currency, new_balance)
        player.touch()

        return ResourceTransaction(
            client_ts=client_ts,
            flow_type="Sink",
            virtual_currency=currency,
            item_type=item_type,
            item_id=item_id,
            amount=-amount,  # signed: Sink luôn < 0
        )

    def random_source(self, player: Any, client_ts: int,
                      currency: str | None = None) -> ResourceTransaction:
        """Sinh 1 transaction Source ngẫu nhiên (tiện cho testing).

        Args:
            player: Player model.
            client_ts: unix timestamp.
            currency: nếu None -> random chọn currency.
        """
        if currency is None:
            currency = self._rng.choice(event_config.RESOURCE_CURRENCIES)
        item_type, item_id = self._pick_item(currency, "Source")
        return self.award(player, currency, item_type, item_id, client_ts)

    def random_sink(self, player: Any, client_ts: int,
                    currency: str | None = None,
                    allow_overdraft: bool = False) -> ResourceTransaction | None:
        """Sinh 1 transaction Sink ngẫu nhiên (tiện cho testing).

        Trả về None nếu player không đủ balance.
        """
        if currency is None:
            currency = self._rng.choice(event_config.RESOURCE_CURRENCIES)
        item_type, item_id = self._pick_item(currency, "Sink")
        return self.spend(
            player, currency, item_type, item_id,
            client_ts, allow_overdraft=allow_overdraft,
        )


__all__ = [
    "EconomySimulator",
    "ResourceTransaction",
    "CURRENCY_FIELD",
]
