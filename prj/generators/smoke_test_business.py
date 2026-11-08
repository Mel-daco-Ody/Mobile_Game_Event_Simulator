"""Smoke test cho BƯỚC 11 – Business Event & Monetization Simulator.

Kiểm tra BusinessGenerator (event payload):
    1. category = 'business'.
    2. Đầy đủ 11 common annotations.
    3. event_id format 'itemType:itemId' (2 phần, 1 dấu ':').
    4. KHÔNG UUID/timestamp/user_id trong event_id.
    5. currency 3-letter UPPERCASE ISO 4217.
    6. Raise ValueError với currency sai format.
    7. amount > 0 (minor unit).
    8. transaction_num khớp lifetime counter của player.
    9. cart_type optional (có thể có hoặc không).
    10. event_id match INSTRUCTION example 'GemPack:GemPack_500'.

Kiểm tra MonetizationSimulator:
    11. try_purchase với non_payer → None (không bao giờ mua).
    12. Sau khi mua: transaction_num += 1, lifetime_revenue += amount.
    13. Whale mua nhiều hơn Low-Payer (statistical, n=200).
    14. event_id = product_type:product_id – KHÔNG có dynamic ID.
    15. Multi-currency: US player → USD, JP player → JPY, ...
    16. Sau khi mua GemPack: player.gems += gem_amount.
    17. Currency conversion đúng (USD 99 → JPY ~ 15543).

Chạy:
    cd prj && python generators/smoke_test_business.py
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import monetization_config  # noqa: E402
from generators import (  # noqa: E402
    generate_business_event,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.monetization import (  # noqa: E402
    MonetizationSimulator,
    PurchaseDecision,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 11: BUSINESS EVENT & MONETIZATION")
    print("=" * 60)

    player = generate_players(count=1, seed=42)[0]
    base_ts = int(time.time())
    session = generate_session(player, session_start_ts=base_ts)

    # ============================================================
    # A. BusinessGenerator – event payload
    # ============================================================
    print("\n[--- A. BUSINESS EVENT PAYLOAD ---]")

    # ------------------------------------------------------------------
    # 1. category = 'business'
    # ------------------------------------------------------------------
    print("\n[1] category = 'business'")
    decision = PurchaseDecision(
        client_ts=base_ts + 10,
        product_id="GemPack_500",
        product_type="GemPack",
        amount=499,
        currency="USD",
        transaction_num=1,
        cart_type="menu_shop",
        gem_amount=500,
    )
    event = generate_business_event(player, session, decision)
    assert_check(event["category"] == "business",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations")
    required_common = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
    }
    required_business = {"event_id", "currency", "amount", "transaction_num"}
    required = required_common | required_business
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields")

    # ------------------------------------------------------------------
    # 3. event_id format 'itemType:itemId' (1 dấu ':')
    # ------------------------------------------------------------------
    print("\n[3] event_id format 'itemType:itemId' (2 phần)")
    assert_check(event["event_id"] == "GemPack:GemPack_500",
                 f"event_id = '{event['event_id']}'")
    assert_check(event["event_id"].count(":") == 1,
                 "event_id có đúng 1 dấu ':'")

    # ------------------------------------------------------------------
    # 4. KHÔNG UUID/timestamp/user_id trong event_id
    # ------------------------------------------------------------------
    print("\n[4] event_id KHÔNG chứa UUID/timestamp/user_id")
    eid = event["event_id"]
    assert_check(player.user_id not in eid,
                 f"event_id KHÔNG chứa user_id '{player.user_id}'")
    uuid_like = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        eid, flags=re.IGNORECASE
    )
    assert_check(uuid_like is None, "event_id KHÔNG chứa UUID")
    assert_check(not re.search(r"\d{10,}", eid),
                 "event_id KHÔNG chứa chuỗi timestamp")

    # ------------------------------------------------------------------
    # 5. currency 3-letter UPPERCASE ISO 4217
    # ------------------------------------------------------------------
    print("\n[5] currency 3-letter UPPERCASE ISO 4217")
    assert_check(event["currency"] == "USD", "currency = 'USD'")
    for cur in ("USD", "EUR", "JPY", "KRW", "GBP"):
        d = PurchaseDecision(base_ts, "GemPack_100", "GemPack", 99,
                             cur, 1, None, 100)
        ev = generate_business_event(player, session, d)
        assert_check(ev["currency"] == cur, f"currency '{cur}' hợp lệ")

    # ------------------------------------------------------------------
    # 6. Raise ValueError với currency sai format
    # ------------------------------------------------------------------
    print("\n[6] ValueError với currency sai format")
    for bad_currency in ("usd", "US", "USDD", "$", "dollar", "us"):
        try:
            d = PurchaseDecision(base_ts, "GemPack_100", "GemPack", 99,
                                 bad_currency, 1, None, 100)
            generate_business_event(player, session, d)
            raise AssertionError(f"Không raise ValueError với currency='{bad_currency}'")
        except ValueError as e:
            assert_check(True, f"ValueError '{bad_currency}': {e}")

    # ------------------------------------------------------------------
    # 7. amount > 0 (minor unit)
    # ------------------------------------------------------------------
    print("\n[7] amount > 0 (minor unit)")
    assert_check(event["amount"] == 499, f"amount = {event['amount']} (minor)")
    for amt in (99, 499, 999, 4999):
        d = PurchaseDecision(base_ts, "GemPack_100", "GemPack", amt,
                             "USD", 1, None, 100)
        ev = generate_business_event(player, session, d)
        assert_check(ev["amount"] == amt, f"amount {amt} OK")

    try:
        bad = PurchaseDecision(base_ts, "GemPack_100", "GemPack", 0,
                               "USD", 1, None, 100)
        generate_business_event(player, session, bad)
        raise AssertionError("Không raise ValueError với amount=0")
    except ValueError as e:
        assert_check(True, f"ValueError amount=0: {e}")

    # ------------------------------------------------------------------
    # 8. transaction_num khớp lifetime counter
    # ------------------------------------------------------------------
    print("\n[8] transaction_num khớp lifetime counter")
    assert_check(event["transaction_num"] == 1,
                 f"transaction_num = {event['transaction_num']}")

    # ------------------------------------------------------------------
    # 9. cart_type optional
    # ------------------------------------------------------------------
    print("\n[9] cart_type optional")
    assert_check(event.get("cart_type") == "menu_shop", "cart_type = menu_shop")
    # Không có cart_type
    d = PurchaseDecision(base_ts, "GemPack_100", "GemPack", 99,
                         "USD", 1, None, 100)
    ev = generate_business_event(player, session, d)
    assert_check("cart_type" not in ev or ev.get("cart_type") is None,
                 "cart_type KHÔNG có trong event khi None")

    # ------------------------------------------------------------------
    # 10. event_id match INSTRUCTION example
    # ------------------------------------------------------------------
    print("\n[10] event_id match INSTRUCTION example 'GemPack:GemPack_500'")
    assert_check(event["event_id"] == "GemPack:GemPack_500",
                 "event_id = 'GemPack:GemPack_500'")

    # Sample payload
    print("\n[Sample] Business event payload:")
    print("  " + json.dumps(event, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. MonetizationSimulator – state & rules
    # ============================================================
    print("\n[--- B. MONETIZATION SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 11. try_purchase với non_payer → None
    # ------------------------------------------------------------------
    print("\n[11] non_payer KHÔNG bao giờ mua")
    p = generate_players(count=1, seed=1)[0]
    # Force non_payer
    p.player_segment = "non_payer"
    pre_txn = p.transaction_num
    sim = MonetizationSimulator(rng=random.Random(0))
    # Thử 100 lần, expect luôn None
    none_count = 0
    for _ in range(100):
        result = sim.try_purchase(p, base_ts)
        if result is None:
            none_count += 1
    assert_check(none_count == 100,
                 f"non_payer 100/100 lần đều None (got {none_count})")
    assert_check(p.transaction_num == pre_txn,
                 f"transaction_num KHÔNG đổi ({pre_txn} == {p.transaction_num})")

    # ------------------------------------------------------------------
    # 12. Sau khi mua: transaction_num += 1, lifetime_revenue += amount
    # ------------------------------------------------------------------
    print("\n[12] Sau khi mua: transaction_num += 1, lifetime_revenue += amount")
    # Force whale để chắc chắn mua
    p = generate_players(count=1, seed=2)[0]
    p.player_segment = "whale"
    p.transaction_num = 5
    p.lifetime_revenue = 0
    pre_txn = p.transaction_num
    pre_rev = p.lifetime_revenue

    class ForceBuyRng:
        def __init__(self, real_rng):
            self._r = real_rng
        def random(self) -> float:
            return 0.0  # luôn < success_prob
        def choice(self, seq):
            return self._r.choice(seq)
        def choices(self, *args, **kwargs):
            return self._r.choices(*args, **kwargs)
        def uniform(self, *args, **kwargs):
            return self._r.uniform(*args, **kwargs)

    sim = MonetizationSimulator(rng=ForceBuyRng(random.Random(0)))
    result = sim.try_purchase(p, base_ts)
    assert_check(result is not None, "whale ForceBuy → mua thành công")
    assert_check(p.transaction_num == pre_txn + 1,
                 f"transaction_num: {pre_txn} -> {p.transaction_num}")
    assert_check(p.lifetime_revenue == pre_rev + result.amount,
                 f"lifetime_revenue: {pre_rev} -> {p.lifetime_revenue} (+{result.amount})")
    assert_check(result.transaction_num == p.transaction_num,
                 "decision.transaction_num khớp player state")

    # ------------------------------------------------------------------
    # 13. Whale mua nhiều hơn Low-Payer (statistical, n=500)
    # ------------------------------------------------------------------
    print("\n[13] Whale mua nhiều hơn Low-Payer (statistical, n=500)")
    rng_low = random.Random(11)
    rng_whale = random.Random(11)
    sim_low = MonetizationSimulator(rng=rng_low)
    sim_whale = MonetizationSimulator(rng=rng_whale)

    count_low = 0
    count_whale = 0
    rev_low = 0
    rev_whale = 0
    for i in range(500):
        pl = generate_players(count=1, seed=1000 + i)[0]
        pl.player_segment = "low_payer"
        pl.transaction_num = 0
        pl.lifetime_revenue = 0
        d = sim_low.try_purchase(pl, base_ts + i)
        if d is not None:
            count_low += 1
            rev_low += d.amount

        pw = generate_players(count=1, seed=1000 + i)[0]
        pw.player_segment = "whale"
        pw.transaction_num = 0
        pw.lifetime_revenue = 0
        d = sim_whale.try_purchase(pw, base_ts + i)
        if d is not None:
            count_whale += 1
            rev_whale += d.amount

    print(f"  [INFO] Low-Payer:  {count_low}/500 purchases, ${rev_low/100:.2f} revenue")
    print(f"  [INFO] Whale:      {count_whale}/500 purchases, ${rev_whale/100:.2f} revenue")
    assert_check(count_whale > count_low,
                 f"Whale ({count_whale}) > Low-Payer ({count_low}) theo spec")
    assert_check(rev_whale > rev_low,
                 f"Whale revenue ({rev_whale}) > Low-Payer ({rev_low})")

    # ------------------------------------------------------------------
    # 14. event_id KHÔNG có dynamic ID (luôn từ catalog)
    # ------------------------------------------------------------------
    print("\n[14] event_id KHÔNG có dynamic ID")
    catalog_ids = {p["product_id"] for p in monetization_config.PRODUCT_CATALOG}
    catalog_types = {p["product_type"] for p in monetization_config.PRODUCT_CATALOG}
    seen_event_ids = set()
    seen_product_ids = set()

    # Dùng mid_payer (weighted_asc) + nhiều seed để tăng variety
    # Whale weighted_desc quá nghiêng về GemPack_10000 nên ít variety.
    for i in range(200):
        p = generate_players(count=1, seed=999 + i)[0]
        p.player_segment = "mid_payer"
        sim_t = MonetizationSimulator(rng=random.Random(1000 + i))
        d = sim_t.try_purchase(p, base_ts)
        if d is not None:
            assert_check(d.product_id in catalog_ids,
                         f"product_id '{d.product_id}' trong catalog")
            assert_check(d.product_type in catalog_types,
                         f"product_type '{d.product_type}' trong catalog")
            seen_event_ids.add(d.event_id)
            seen_product_ids.add(d.product_id)
            # event_id KHÔNG chứa UUID/timestamp/user_id
            assert_check(p.user_id not in d.event_id,
                         "event_id KHÔNG chứa user_id")
            assert_check(not re.search(r"\d{10,}", d.event_id),
                         "event_id KHÔNG chứa timestamp")

    # Phải thấy >= 3 product khác nhau (variety đủ) trong 200 trials
    assert_check(len(seen_product_ids) >= 3,
                 f"Variety >= 3 product_ids: {seen_product_ids}")
    # Mỗi event_id đều match format '<type>:<id>' với 1 dấu ':'
    for eid in seen_event_ids:
        assert_check(eid.count(":") == 1, f"event_id '{eid}' có 1 dấu ':'")
    print(f"  [INFO] Đã thấy {len(seen_product_ids)} product_ids khác nhau")

    # ------------------------------------------------------------------
    # 15. Multi-currency theo country
    # ------------------------------------------------------------------
    print("\n[15] Multi-currency theo country")
    expected_currency = {
        "US": "USD", "JP": "JPY", "KR": "KRW", "GB": "GBP",
        "DE": "EUR", "FR": "EUR", "VN": "USD", "ID": "USD",
    }
    for country, want_currency in expected_currency.items():
        p = generate_players(count=1, seed=hash(country) & 0xFFFFFFFF)[0]
        p.player_segment = "whale"
        p.country = country
        # Tìm player có country đúng (generate_players random theo weight)
        for attempt in range(200):
            p2 = generate_players(count=1, seed=attempt * 13 + hash(country))[
                0]
            if p2.country == country:
                p2.player_segment = "whale"
                sim_c = MonetizationSimulator(rng=ForceBuyRng(random.Random(0)))
                d = sim_c.try_purchase(p2, base_ts)
                if d is not None:
                    assert_check(d.currency == want_currency,
                                 f"{country} -> '{d.currency}' (want '{want_currency}')")
                else:
                    # Nếu không mua được thì currency mặc định của conversion test
                    pass
                break

    # ------------------------------------------------------------------
    # 16. Sau khi mua GemPack: player.gems += gem_amount
    # ------------------------------------------------------------------
    print("\n[16] Sau khi mua GemPack: player.gems += gem_amount")
    # Whale có thể chọn Boost product (gem_amount=0). Tìm trial có gem > 0.
    pre_gems = 0
    found_with_gem = False
    for seed_offset in range(50):
        p = generate_players(count=1, seed=33 + seed_offset)[0]
        p.player_segment = "whale"
        p.country = "US"
        pre_gems = p.gems
        sim = MonetizationSimulator(rng=ForceBuyRng(random.Random(seed_offset)))
        d = sim.try_purchase(p, base_ts)
        if d is not None and d.gem_amount > 0:
            assert_check(True,
                         f"Mua thành công {d.product_id} có gem = {d.gem_amount}")
            assert_check(p.gems == pre_gems + d.gem_amount,
                         f"gems: {pre_gems} -> {p.gems} (+{d.gem_amount})")
            found_with_gem = True
            break
    assert_check(found_with_gem,
                 "Tìm được ít nhất 1 trial whale mua GemPack có gem > 0")

    # ------------------------------------------------------------------
    # 17. Currency conversion đúng (USD 99 → JPY ~ 15543)
    # ------------------------------------------------------------------
    print("\n[17] Currency conversion đúng")
    # Sim trực tiếp conversion (USD 99 -> JPY)
    sim_test = MonetizationSimulator(rng=random.Random(0))
    usd_to_jpy = sim_test._convert_price(99, "USD", "JPY")
    # 99 USD * 157 = 15543
    expected = int(99 * 157.0)
    assert_check(usd_to_jpy == expected,
                 f"USD 99 -> JPY {usd_to_jpy} (want {expected})")
    # USD 499 -> EUR ~ 464
    usd_to_eur = sim_test._convert_price(499, "USD", "EUR")
    expected_eur = int(499 * 0.93)
    assert_check(usd_to_eur == expected_eur,
                 f"USD 499 -> EUR {usd_to_eur} (want {expected_eur})")
    # USD -> USD = giữ nguyên
    usd_to_usd = sim_test._convert_price(99, "USD", "USD")
    assert_check(usd_to_usd == 99, "USD -> USD giữ nguyên = 99")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 11 BUSINESS OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
