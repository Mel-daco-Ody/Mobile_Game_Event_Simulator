"""Monetization Simulator – quản lý IAP business event logic.

Theo BƯỚC 11 INSTRUCTION.md:

Phát sinh event 'business' cho real-money purchase.

Required fields (trên mỗi business event):
    - event_id     : 'itemType:itemId' (ví dụ 'GemPack:GemPack_500')
    - amount       : integer theo đơn vị nhỏ nhất của currency
                     ($0.99 -> 99, $4.99 -> 499)
    - currency     : 3-letter UPPERCASE (USD, EUR, JPY, ...)
                     KHÔNG dùng 'usd', '$', 'dollar'
    - transaction_num: lifetime counter (KHÔNG reset mỗi session)

Product catalog cố định (trong monetization_config):
    GemPack_100, GemPack_500, GemPack_1200,
    StarterPack, BattlePass, PremiumBundle, MegaBoost, SmallBoost

KHÔNG tạo dynamic event ID (GemPack:100:UUID:timestamp).

Logic payer:
    Non-Payer : KHÔNG có business event
    Low-Payer : rất hiếm
    Mid-Payer : trung bình
    Whale     : cao
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from config import monetization_config, player_config


# =========================================================================
# Country -> currency mapping
# =========================================================================
# Theo country code (ISO 3166-1 alpha-2) -> ISO 4217 currency code
# Phù hợp với CURRENCY_CONVERSION trong monetization_config.
_COUNTRY_TO_CURRENCY: dict[str, str] = {
    "US": "USD",
    "JP": "JPY",
    "KR": "KRW",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    # VN/ID/TH/BR -> USD (mobile game thường bill USD qua store)
    "VN": "USD",
    "ID": "USD",
    "TH": "USD",
    "BR": "USD",
}


def _country_to_currency(country: str) -> str:
    """Map country code -> ISO currency code. Mặc định USD."""
    return _COUNTRY_TO_CURRENCY.get(country, "USD")


# =========================================================================
# Purchase decision (nội bộ – truyền cho generator)
# =========================================================================

@dataclass
class PurchaseDecision:
    """Quyết định mua IAP – được generator chuyển thành event 'business'.

    Attributes:
        client_ts: unix timestamp.
        product_id: id sản phẩm ('GemPack_500', 'BattlePass', ...).
        product_type: loại ('GemPack', 'Pack', 'Subscription', ...).
        item_type: phần đầu của event_id (= product_type).
        item_id: phần sau của event_id (= product_id).
        amount: integer theo minor unit của currency.
        currency: 3-letter UPPERCASE.
        transaction_num: lifetime counter SAU khi mua (đã +1).
        cart_type: optional ('menu_shop', 'end_of_level_shop',
                    'special_offer_popup'). Có thể None.
        gem_amount: số gem cộng cho player (0 nếu product không có gem).
    """

    client_ts: int
    product_id: str
    product_type: str
    amount: int
    currency: str
    transaction_num: int
    cart_type: str | None = None
    gem_amount: int = 0

    @property
    def item_type(self) -> str:
        """Phần đầu của event_id (= product_type)."""
        return self.product_type

    @property
    def item_id(self) -> str:
        """Phần sau của event_id (= product_id)."""
        return self.product_id

    @property
    def event_id(self) -> str:
        """Build event_id theo INSTRUCTION: 'itemType:itemId'.

        Ví dụ: 'GemPack:GemPack_500'. KHÔNG chứa UUID/timestamp/user_id.
        """
        return f"{self.product_type}:{self.product_id}"


# =========================================================================
# Monetization Simulator
# =========================================================================

class MonetizationSimulator:
    """Quản lý IAP cho 1 player xuyên suốt simulation.

    Instance state:
        self._rng: random.Random instance.
        self._catalog: list[dict] – mirror của PRODUCT_CATALOG.
    """

    # Whale / Mid-Payer có xu hướng chọn sản phẩm giá trị cao hơn
    # (weighted theo price thay vì uniform).
    _WEIGHT_BY_PRICE: dict[str, str] = {
        "non_payer": "none",
        "low_payer": "uniform",
        "mid_payer": "weighted_asc",
        "whale": "weighted_desc",   # whale ưu tiên pack lớn
    }

    def __init__(self, rng: random.Random | None = None) -> None:
        if rng is None:
            rng = random.Random()
        self._rng = rng
        # Snapshot catalog để tránh mutate nếu consumer muốn customize
        self._catalog: list[dict[str, Any]] = [
            dict(p) for p in monetization_config.PRODUCT_CATALOG
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def try_purchase(self, player: Any, client_ts: int,
                     cart_type: str | None = None) -> PurchaseDecision | None:
        """Thử mua IAP cho player tại thời điểm client_ts.

        Quyết định:
            1. Non-Payer → KHÔNG mua (return None).
            2. Lấy xác suất theo player_segment từ
               IAP_PURCHASE_PROBABILITY_PER_SESSION. Sample: roll dice.
            3. Nếu roll fail → return None.
            4. Nếu roll success → chọn product theo segment weight,
               sinh PurchaseDecision, gọi player.increment_transaction()
               để cập nhật transaction_num + lifetime_revenue, cộng gems
               nếu có.

        Args:
            player: Player model (sẽ bị mutate: transaction_num,
                    lifetime_revenue, gems nếu gem_amount > 0).
            client_ts: unix timestamp.
            cart_type: nếu None -> random từ CART_TYPES.

        Returns:
            PurchaseDecision (nếu player mua), hoặc None nếu không mua.
        """
        segment = player.player_segment

        # 1. Non-payer → không bao giờ mua
        if segment == "non_payer":
            return None

        # 2. Sample xác suất
        prob = monetization_config.IAP_PURCHASE_PROBABILITY_PER_SESSION[segment]
        if prob <= 0:
            return None
        if self._rng.random() >= prob:
            return None

        # 3. Chọn product theo segment
        product = self._pick_product(segment)
        if product is None:
            return None

        # 4. cart_type
        if cart_type is None:
            cart_type = self._rng.choice(monetization_config.CART_TYPES)

        # 5. Tính amount theo currency của player
        target_currency = _country_to_currency(player.country)
        amount_minor = self._convert_price(product["price"],
                                           product["currency"],
                                           target_currency)

        # 6. Cập nhật player state qua helper đã có sẵn
        player.increment_transaction(amount_minor)

        # Cộng gems nếu gem_amount > 0
        gem_amount = int(product.get("gem_amount", 0))
        if gem_amount > 0:
            player.gems += gem_amount
            player.touch()

        # 7. Trả PurchaseDecision
        return PurchaseDecision(
            client_ts=client_ts,
            product_id=product["product_id"],
            product_type=product["product_type"],
            amount=amount_minor,
            currency=target_currency,
            transaction_num=player.transaction_num,
            cart_type=cart_type,
            gem_amount=gem_amount,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pick_product(self, segment: str) -> dict[str, Any] | None:
        """Chọn product theo segment.

        Strategy:
            low_payer  -> uniform
            mid_payer  -> weighted by price ascending (ưu tiên pack nhỏ)
            whale      -> weighted by price descending (ưu tiên pack lớn)
        """
        if not self._catalog:
            return None

        strategy = self._WEIGHT_BY_PRICE.get(segment, "uniform")
        if strategy == "uniform":
            return self._rng.choice(self._catalog)

        # weighted by price
        prices = [p["price"] for p in self._catalog]
        if strategy == "weighted_asc":
            weights = [1.0 / (p + 1) for p in prices]
        elif strategy == "weighted_desc":
            weights = [float(p) for p in prices]
        else:
            weights = [1.0] * len(prices)

        return self._rng.choices(self._catalog, weights=weights, k=1)[0]

    def _convert_price(self, price_minor: int, source_currency: str,
                       target_currency: str) -> int:
        """Convert price giữa 2 currency qua USD trung gian.

        Sử dụng CURRENCY_CONVERSION:
            - USD -> target : multiply by target_rate
            - source -> USD : divide by source_rate
            - source -> target : convert source -> USD -> target

        Returns:
            int theo minor unit của target_currency (làm tròn xuống).
        """
        if source_currency == target_currency:
            return price_minor

        rates = monetization_config.CURRENCY_CONVERSION
        if source_currency not in rates or target_currency not in rates:
            # Fallback: giữ nguyên giá
            return price_minor

        # Đưa về USD: USD = price / source_rate
        usd = price_minor / rates[source_currency]
        # Convert sang target: target = usd * target_rate
        converted = usd * rates[target_currency]
        return int(converted)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def get_product(self, product_id: str) -> dict[str, Any] | None:
        """Tra cứu product theo id."""
        for p in self._catalog:
            if p["product_id"] == product_id:
                return p
        return None


__all__ = [
    "MonetizationSimulator",
    "PurchaseDecision",
]
