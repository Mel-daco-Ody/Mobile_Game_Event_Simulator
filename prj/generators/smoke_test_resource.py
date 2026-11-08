"""Smoke test cho BƯỚC 10 – Resource Event & Economy Simulator.

Kiểm tra ResourceGenerator (event payload):
    1. category = 'resource'.
    2. Đầy đủ 11 common annotations.
    3. event_id format: '<flow>:<currency>:<item_type>:<item_id>'.
    4. KHÔNG chứa UUID/timestamp/user_id trong event_id.
    5. flow_type chỉ là Source / Sink.
    6. virtual_currency ∈ {Coins, Gems, Energy, Lives, Stars, Tickets}.
    7. amount signed: Source > 0, Sink < 0.
    8. Raise ValueError nếu amount sai dấu.

Kiểm tra EconomySimulator:
    9. award() cộng balance đúng + trả 1 transaction với amount > 0.
    10. spend() trừ balance đúng + amount < 0.
    11. spend() không đủ balance → None, KHÔNG spam event, balance KHÔNG đổi.
    12. Quy tắc chống spam: 1 award +500 = 1 transaction (không phải 500 tx).
    13. Reproducible: cùng seed → cùng amount.
    14. 6 currencies đều hoạt động (Source + Sink).
    15. event_id match INSTRUCTION example 'Source:Coins:Gameplay:LevelReward'.

Chạy:
    cd prj && python generators/smoke_test_resource.py
"""

from __future__ import annotations

import json
import os
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

from config import event_config, player_config  # noqa: E402
from generators import (  # noqa: E402
    generate_players,
    generate_resource_event,
)
from models.session import generate_session  # noqa: E402
from simulation.economy import (  # noqa: E402
    EconomySimulator,
    ResourceTransaction,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 10: RESOURCE EVENT & ECONOMY")
    print("=" * 60)

    player = generate_players(count=1, seed=42)[0]
    # Reset balance để test deterministic
    player.coins = 1000
    player.gems = 50
    player.energy = 30
    player.lives = 5
    player.stars = 0
    player.tickets = 1

    base_ts = int(time.time())
    session = generate_session(player, session_start_ts=base_ts)

    # ============================================================
    # A. ResourceGenerator – event payload
    # ============================================================
    print("\n[--- A. RESOURCE EVENT PAYLOAD ---]")

    # ------------------------------------------------------------------
    # 1. category = 'resource'
    # ------------------------------------------------------------------
    print("\n[1] category = 'resource'")
    tx = ResourceTransaction(
        client_ts=base_ts + 10,
        flow_type="Source",
        virtual_currency="Coins",
        item_type="Gameplay",
        item_id="LevelReward",
        amount=500,
    )
    event = generate_resource_event(player, session, tx)
    assert_check(event["category"] == "resource",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations + 5 resource fields")
    required = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
        "event_id", "flow_type", "virtual_currency",
        "item_type", "item_id", "amount",
    }
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields: {sorted(event.keys())}")

    # ------------------------------------------------------------------
    # 3. event_id format '<flow>:<currency>:<item_type>:<item_id>'
    # ------------------------------------------------------------------
    print("\n[3] event_id format đúng theo INSTRUCTION")
    assert_check(event["event_id"] == "Source:Coins:Gameplay:LevelReward",
                 f"event_id = '{event['event_id']}'")
    assert_check(event["event_id"].count(":") == 3,
                 "event_id có đúng 4 phần (3 dấu ':')")

    # ------------------------------------------------------------------
    # 4. event_id KHÔNG chứa UUID/timestamp/user_id
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
                 "event_id KHÔNG chứa chuỗi timestamp (>= 10 chữ số liên tiếp)")

    # ------------------------------------------------------------------
    # 5. flow_type chỉ là Source / Sink
    # ------------------------------------------------------------------
    print("\n[5] flow_type chỉ là Source / Sink")
    for flow in ("Source", "Sink"):
        signed = 500 if flow == "Source" else -100
        tx = ResourceTransaction(base_ts, flow, "Coins", "Gameplay",
                                 "LevelReward", signed)
        ev = generate_resource_event(player, session, tx)
        assert_check(ev["flow_type"] in ("Source", "Sink"),
                     f"flow_type = '{flow}' hợp lệ")
    # Invalid flow
    try:
        bad_tx = ResourceTransaction(base_ts, "InvalidFlow", "Coins",
                                     "Gameplay", "LevelReward", 100)
        generate_resource_event(player, session, bad_tx)
        raise AssertionError("Không raise ValueError với flow_type không hợp lệ")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ------------------------------------------------------------------
    # 6. virtual_currency ∈ 6 currencies
    # ------------------------------------------------------------------
    print("\n[6] virtual_currency ∈ {Coins, Gems, Energy, Lives, Stars, Tickets}")
    valid_currencies = {"Coins", "Gems", "Energy", "Lives", "Stars", "Tickets"}
    assert_check(set(event_config.RESOURCE_CURRENCIES) == valid_currencies,
                 f"6 currencies: {event_config.RESOURCE_CURRENCIES}")

    # ------------------------------------------------------------------
    # 7. amount signed: Source > 0, Sink < 0
    # ------------------------------------------------------------------
    print("\n[7] amount signed đúng (Source > 0, Sink < 0)")
    src_ev = generate_resource_event(
        player, session,
        ResourceTransaction(base_ts, "Source", "Gems", "Purchase",
                            "GemPack", 100),
    )
    sink_ev = generate_resource_event(
        player, session,
        ResourceTransaction(base_ts, "Sink", "Coins", "Shop",
                           "HealthPotion", -150),
    )
    assert_check(src_ev["amount"] > 0, f"Source amount = {src_ev['amount']} > 0")
    assert_check(sink_ev["amount"] < 0, f"Sink amount = {sink_ev['amount']} < 0")

    # ------------------------------------------------------------------
    # 8. Raise ValueError nếu amount sai dấu
    # ------------------------------------------------------------------
    print("\n[8] ValueError khi amount sai dấu")
    try:
        # Source nhưng amount âm
        bad_src = ResourceTransaction(base_ts, "Source", "Coins", "Gameplay",
                                      "LevelReward", -100)
        generate_resource_event(player, session, bad_src)
        raise AssertionError("Không raise ValueError cho Source amount < 0")
    except ValueError as e:
        assert_check(True, f"ValueError (Source <= 0): {e}")

    try:
        # Sink nhưng amount dương
        bad_sink = ResourceTransaction(base_ts, "Sink", "Coins", "Shop",
                                       "HealthPotion", 100)
        generate_resource_event(player, session, bad_sink)
        raise AssertionError("Không raise ValueError cho Sink amount > 0")
    except ValueError as e:
        assert_check(True, f"ValueError (Sink >= 0): {e}")

    # Sample payload
    print("\n[Sample] Resource event payload:")
    print("  " + json.dumps(event, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. EconomySimulator – state & rules
    # ============================================================
    print("\n[--- B. ECONOMY SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 9. award() cộng balance + amount > 0
    # ------------------------------------------------------------------
    print("\n[9] award() cộng balance + amount > 0")
    p = generate_players(count=1, seed=42)[0]
    p.coins = 0
    sim = EconomySimulator(rng=__import__("random").Random(0))
    initial_coins = p.coins
    tx = sim.award(p, "Coins", "Gameplay", "LevelReward", client_ts=base_ts + 5)
    assert_check(p.coins > initial_coins,
                 f"Coins: {initial_coins} -> {p.coins}")
    assert_check(tx.amount > 0, f"Source amount = {tx.amount} > 0")
    assert_check(p.coins == initial_coins + tx.amount,
                 f"p.coins == initial + tx.amount ({initial_coins} + {tx.amount} = {p.coins})")

    # ------------------------------------------------------------------
    # 10. spend() trừ balance + amount < 0
    # ------------------------------------------------------------------
    print("\n[10] spend() trừ balance + amount < 0")
    p.coins = 1000
    pre = p.coins
    tx = sim.spend(p, "Coins", "Shop", "HealthPotion", client_ts=base_ts + 6)
    assert_check(tx is not None and tx.amount < 0,
                 f"Sink amount = {tx.amount} < 0")
    assert_check(p.coins == pre - abs(tx.amount),
                 f"p.coins == pre - |amount| ({pre} - {abs(tx.amount)} = {p.coins})")

    # ------------------------------------------------------------------
    # 11. spend() không đủ balance → None, balance KHÔNG đổi
    # ------------------------------------------------------------------
    print("\n[11] spend() không đủ balance → None (anti-spam)")
    p.coins = 5  # rất ít
    pre_coins = p.coins
    # Cố spend HealthPotion 50-150 (sẽ lấy min=50, vẫn > 5)
    # Dùng amount cố định để chắc chắn > balance
    tx = sim.spend(p, "Coins", "Shop", "HealthPotion",
                   client_ts=base_ts + 7, amount=1000)
    assert_check(tx is None,
                 f"spend() không đủ balance → None, got={tx}")
    assert_check(p.coins == pre_coins,
                 f"Balance KHÔNG đổi: {pre_coins} == {p.coins}")
    # Đây là anti-spam: không tạo event Sink thất bại

    # ------------------------------------------------------------------
    # 12. Quy tắc chống spam: +500 = 1 transaction (KHÔNG phải 500)
    # ------------------------------------------------------------------
    print("\n[12] Quy tắc chống spam: +500 = 1 transaction")
    p.coins = 0
    sim_anti = EconomySimulator(rng=__import__("random").Random(0))
    tx = sim_anti.award(p, "Coins", "Gameplay", "LevelReward",
                        client_ts=base_ts + 8, amount=500)
    # Tx này là MỘT giao dịch với amount=500, KHÔNG phải 500 tx +1
    assert_check(tx.amount == 500, f"1 transaction, amount = {tx.amount}")
    assert_check(p.coins == 500, f"Balance += 500 (1 lần), {p.coins}")
    # Tạo event từ tx – chỉ phát MỘT event
    ev = generate_resource_event(player, session, tx)
    assert_check(ev["amount"] == 500, f"Event amount = {ev['amount']} (1 event)")

    # ------------------------------------------------------------------
    # 13. Reproducible: cùng seed → cùng amount
    # ------------------------------------------------------------------
    print("\n[13] Reproducible: cùng seed → cùng amount")
    def get_amount(seed_val: int) -> int:
        sim_r = EconomySimulator(rng=__import__("random").Random(seed_val))
        p = generate_players(count=1, seed=seed_val)[0]
        p.coins = 0
        tx = sim_r.award(p, "Coins", "Gameplay", "LevelReward",
                         client_ts=base_ts)
        return tx.amount

    amt_a = get_amount(42)
    amt_b = get_amount(42)
    assert_check(amt_a == amt_b, f"Reproducible: {amt_a} == {amt_b}")

    # ------------------------------------------------------------------
    # 14. 6 currencies đều hoạt động
    # ------------------------------------------------------------------
    print("\n[14] 6 currencies đều hoạt động (Source + Sink)")
    for currency in event_config.RESOURCE_CURRENCIES:
        p = generate_players(count=1, seed=hash(currency) & 0xFFFFFFFF)[0]
        # Force segment mong muốn
        # Set balance ban đầu cao để spend không bao giờ fail
        attr = currency.lower()
        setattr(p, attr, 10000)

        sim_c = EconomySimulator(rng=__import__("random").Random(0))
        # Source
        tx_src = sim_c.random_source(p, base_ts, currency=currency)
        assert_check(tx_src.virtual_currency == currency,
                     f"{currency} Source: virtual_currency OK")
        assert_check(tx_src.amount > 0, f"{currency} Source amount > 0")
        # Sink
        tx_sink = sim_c.random_sink(p, base_ts, currency=currency)
        assert_check(tx_sink is not None,
                     f"{currency} Sink (balance cao) → not None")
        if tx_sink is not None:
            assert_check(tx_sink.virtual_currency == currency,
                         f"{currency} Sink: virtual_currency OK")
            assert_check(tx_sink.amount < 0, f"{currency} Sink amount < 0")

    # ------------------------------------------------------------------
    # 15. event_id match INSTRUCTION example
    # ------------------------------------------------------------------
    print("\n[15] event_id match INSTRUCTION example")
    # INSTRUCTION examples:
    #   "Source:Coins:Gameplay:LevelReward" → +500
    #   "Sink:Coins:Shop:HealthPotion" → -100
    #   "Source:Gems:Purchase:GemPack"
    #   "Sink:Gems:Shop:PremiumItem"
    expected_examples = {
        ("Source", "Coins", "Gameplay", "LevelReward"),
        ("Sink", "Coins", "Shop", "HealthPotion"),
        ("Source", "Gems", "Purchase", "GemPack"),
        ("Sink", "Gems", "Shop", "PremiumItem"),
    }
    found = set()
    for currency, flows in event_config.RESOURCE_ITEMS.items():
        for flow_name, items in flows.items():
            cap_flow = flow_name.capitalize()
            for item_type, item_id in items:
                eid = f"{cap_flow}:{currency}:{item_type}:{item_id}"
                if (cap_flow, currency, item_type, item_id) in expected_examples:
                    found.add((cap_flow, currency, item_type, item_id))
                    # Verify format
                    assert_check(eid.count(":") == 3,
                                 f"event_id '{eid}' có 4 phần")

    missing = expected_examples - found
    assert_check(not missing,
                 f"Tất cả INSTRUCTION example đều match: {found}")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 10 RESOURCE OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
