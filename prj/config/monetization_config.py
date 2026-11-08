"""Cấu hình monetization: IAP product catalog và ad.

Định nghĩa product catalog cố định theo section 24 INSTRUCTION.
KHÔNG tạo dynamic event ID, KHÔNG đưa UUID/timestamp vào event_id.
"""

# === Product catalog ===
# Mỗi product: product_id, product_type, price (minor unit), currency
# Giá theo đơn vị nhỏ nhất: $0.99 -> 99, $4.99 -> 499
PRODUCT_CATALOG = [
    # Gem packs
    {"product_id": "GemPack_100",   "product_type": "GemPack",
     "price": 99,   "currency": "USD", "gem_amount": 100},
    {"product_id": "GemPack_500",   "product_type": "GemPack",
     "price": 499,  "currency": "USD", "gem_amount": 500},
    {"product_id": "GemPack_1200",  "product_type": "GemPack",
     "price": 999,  "currency": "USD", "gem_amount": 1200},
    {"product_id": "GemPack_3000",  "product_type": "GemPack",
     "price": 1999, "currency": "USD", "gem_amount": 3000},
    {"product_id": "GemPack_10000", "product_type": "GemPack",
     "price": 4999, "currency": "USD", "gem_amount": 10000},
    # Special packs
    {"product_id": "StarterPack",   "product_type": "Pack",
     "price": 299,  "currency": "USD", "gem_amount": 300},
    {"product_id": "BattlePass",    "product_type": "Subscription",
     "price": 999,  "currency": "USD", "gem_amount": 0},
    {"product_id": "PremiumBundle", "product_type": "Bundle",
     "price": 1499, "currency": "USD", "gem_amount": 1500},
    {"product_id": "MegaBoost",     "product_type": "Boost",
     "price": 199,  "currency": "USD", "gem_amount": 0},
    {"product_id": "SmallBoost",    "product_type": "Boost",
     "price": 99,   "currency": "USD", "gem_amount": 0},
]

# Multi-currency: mở rộng catalog cho EUR, JPY, KRW...
# Mapping currency -> conversion rate (so với USD)
CURRENCY_CONVERSION = {
    "USD": 1.00,
    "EUR": 0.93,
    "JPY": 157.0,    # 1 USD ~ 157 JPY
    "KRW": 1380.0,   # 1 USD ~ 1380 KRW
    "GBP": 0.79,
}

# ISO-4217 currency codes hợp lệ (3-letter UPPERCASE)
VALID_CURRENCIES = list(CURRENCY_CONVERSION.keys())

# === Cart type (optional business field) ===
CART_TYPES = ["menu_shop", "end_of_level_shop", "special_offer_popup"]

# === IAP conversion rate theo payer segment ===
# Non-Payer: 0 (không mua)
# Low-Payer: rất thấp
# Mid-Payer: trung bình
# Whale: cao
IAP_PURCHASE_PROBABILITY_PER_SESSION = {
    "non_payer": 0.000,
    "low_payer": 0.005,
    "mid_payer": 0.030,
    "whale":     0.120,
}

# Số purchase trung bình / tuần cho mỗi payer segment
IAP_PURCHASES_PER_WEEK = {
    "non_payer": 0,
    "low_payer": (0.0, 0.5),
    "mid_payer": (0.5, 2.0),
    "whale":     (3.0, 10.0),
}

# Số gem mỗi USD (để tính gem reward cho product)
GEM_PER_USD = 100

# === Ad monetization ===
# Ad eCPM theo country (mặc định USD)
ECPM_BY_COUNTRY = {
    "US": 8.0, "JP": 6.0, "KR": 5.0, "GB": 5.0, "DE": 4.5,
    "FR": 3.5, "VN": 1.0, "ID": 0.8, "TH": 0.7, "BR": 1.2,
}

# Ad ARPDAU theo engagement segment (USD)
AD_ARPDAU = {
    "casual":   0.005,
    "regular":  0.012,
    "engaged":  0.025,
    "hardcore": 0.040,
}
