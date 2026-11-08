"""Smoke test cho BƯỚC 12 – Ad Event & Ad Simulator.

Kiểm tra AdGenerator (event payload):
    1. category = 'ad'.
    2. Đầy đủ 11 common annotations.
    3. event_id format 'ad:<type>'.
    4. ad_type ∈ {rewarded, interstitial, banner}.
    5. rewarded là bool, đồng bộ với ad_type (chỉ True khi rewarded).
    6. coin_reward chỉ có khi rewarded=True.
    7. Raise ValueError với ad_type không hợp lệ / rewarded ko đồng bộ.

Kiểm tra AdSimulator:
    8. try_session_ad() roll dice theo engagement.
    9. Hardcore roll nhiều hơn casual (statistical).
    10. rewarded có coin_reward trong (50, 200).
    11. eCPM đúng theo ad_type + country.
    12. try_session_ads trả list, có thể rỗng.
    13. Anti-spam: n_max giới hạn.
    14. event_id match INSTRUCTION ad flow.

Kiểm tra rewarded + resource liên kết:
    15. Rewarded → sinh 2 events (ad + resource source).
    16. Resource event_id = 'Source:Coins:Ad:Rewarded'.
    17. Coins balance += coin_reward sau khi economy.award().

Chạy:
    cd prj && python generators/smoke_test_ad.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# prj/ là thư mục parent của generators/ – chứa models/, config/, ...
# Ví dụ: c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import event_config, monetization_config  # noqa: E402
from generators import (  # noqa: E402
    build_rewarded_resource_tx,
    generate_ad_event,
    generate_business_event,
    generate_players,
    generate_resource_event,
    generate_one_player,
    summarize_players,
)
from models.session import generate_session  # noqa: E402
from simulation.ad import (  # noqa: E402
    AD_PLACEMENTS,
    AdDecision,
    AdSimulator,
)
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
    print("SMOKE TEST – BƯỚC 12: AD EVENT & AD SIMULATOR")
    print("=" * 60)

    player = generate_players(count=1, seed=42)[0]
    base_ts = int(time.time())
    session = generate_session(player, session_start_ts=base_ts)

    # ============================================================
    # A. AdGenerator – event payload
    # ============================================================
    print("\n[--- A. AD EVENT PAYLOAD ---]")

    # ------------------------------------------------------------------
    # 1. category = 'design' (ad được map sang design event theo GA spec)
    # ------------------------------------------------------------------
    print("\n[1] category = 'design' (GA spec không có 'ad')")
    decision = AdDecision(
        client_ts=base_ts + 10,
        ad_type="rewarded",
        ad_event_id="ad:rewarded",  # legacy, generator sẽ build từ placement
        placement="end_of_level",
        rewarded=True,
        coin_reward=150,
        eCPM_estimated=4.0,
    )
    event = generate_ad_event(player, session, decision)
    assert_check(event["category"] == "design",
                 f"category = '{event['category']}' (mapped từ 'ad' sang 'design')")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations + ad fields
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations + ad fields")
    required = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
        "event_id", "ad_type", "placement", "rewarded", "coin_reward",
        "value",  # GA design event có value optional
    }
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields")

    # ------------------------------------------------------------------
    # 3. event_id format 'Ad:<type>:<placement>' (GA design spec)
    # ------------------------------------------------------------------
    print("\n[3] event_id format 'Ad:<ad_type>:<placement>'")
    assert_check(event["event_id"] == "Ad:rewarded:end_of_level",
                 f"event_id = '{event['event_id']}'")
    for ad_type in ("rewarded", "interstitial", "banner"):
        d = AdDecision(base_ts, ad_type, f"ad:{ad_type}", "gameplay_idle",
                       rewarded=(ad_type == "rewarded"),
                       coin_reward=(150 if ad_type == "rewarded" else 0))
        ev = generate_ad_event(player, session, d)
        assert_check(ev["event_id"] == f"Ad:{ad_type}:gameplay_idle",
                     f"event_id = 'Ad:{ad_type}:gameplay_idle'")

    # ------------------------------------------------------------------
    # 4. ad_type ∈ {rewarded, interstitial, banner}
    # ------------------------------------------------------------------
    print("\n[4] ad_type ∈ {rewarded, interstitial, banner}")
    assert_check(set(event_config.AD_TYPES) == {"rewarded", "interstitial", "banner"},
                 f"AD_TYPES = {event_config.AD_TYPES}")

    # ------------------------------------------------------------------
    # 5. rewarded là bool, đồng bộ với ad_type
    # ------------------------------------------------------------------
    print("\n[5] rewarded là bool, chỉ True khi ad_type='rewarded'")
    # rewarded ad
    d = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                   rewarded=True, coin_reward=120)
    ev = generate_ad_event(player, session, d)
    assert_check(ev["rewarded"] is True, "rewarded ad có rewarded=True")
    # interstitial ad
    d = AdDecision(base_ts, "interstitial", "ad:interstitial", "end_of_level",
                   rewarded=False, coin_reward=0)
    ev = generate_ad_event(player, session, d)
    assert_check(ev["rewarded"] is False, "interstitial ad có rewarded=False")
    # banner ad
    d = AdDecision(base_ts, "banner", "ad:banner", "gameplay_idle",
                   rewarded=False, coin_reward=0)
    ev = generate_ad_event(player, session, d)
    assert_check(ev["rewarded"] is False, "banner ad có rewarded=False")

    # ------------------------------------------------------------------
    # 6. coin_reward chỉ có khi rewarded=True
    # ------------------------------------------------------------------
    print("\n[6] coin_reward chỉ có khi rewarded=True")
    # rewarded: có coin_reward field
    d = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                   rewarded=True, coin_reward=180)
    ev = generate_ad_event(player, session, d)
    assert_check(ev.get("coin_reward") == 180, "rewarded: coin_reward=180")
    # non-rewarded: KHÔNG có coin_reward
    d = AdDecision(base_ts, "interstitial", "ad:interstitial", "end_of_level",
                   rewarded=False, coin_reward=0)
    ev = generate_ad_event(player, session, d)
    assert_check("coin_reward" not in ev,
                 "interstitial: KHÔNG có coin_reward field")

    # ------------------------------------------------------------------
    # 7. ValueError với ad_type / rewarded không đồng bộ
    # ------------------------------------------------------------------
    print("\n[7] ValueError với ad_type / rewarded không đồng bộ")
    # rewarded=True nhưng ad_type=interstitial
    try:
        d = AdDecision(base_ts, "interstitial", "ad:interstitial",
                       "end_of_level", rewarded=True, coin_reward=100)
        generate_ad_event(player, session, d)
        raise AssertionError("Không raise với rewarded=True + ad_type=interstitial")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ad_type=rewarded nhưng rewarded=False
    try:
        d = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                       rewarded=False, coin_reward=0)
        generate_ad_event(player, session, d)
        raise AssertionError("Không raise với ad_type=rewarded + rewarded=False")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # Non-rewarded có coin_reward > 0
    try:
        d = AdDecision(base_ts, "banner", "ad:banner", "end_of_level",
                       rewarded=False, coin_reward=50)
        generate_ad_event(player, session, d)
        raise AssertionError("Không raise với banner có coin_reward>0")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # coin_reward < 0
    try:
        d = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                       rewarded=True, coin_reward=-10)
        generate_ad_event(player, session, d)
        raise AssertionError("Không raise với coin_reward<0")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ad_type không hợp lệ
    try:
        d = AdDecision(base_ts, "popup", "ad:popup", "end_of_level",
                       rewarded=False, coin_reward=0)
        generate_ad_event(player, session, d)
        raise AssertionError("Không raise với ad_type='popup'")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # Sample payload
    print("\n[Sample] Ad event (rewarded) payload:")
    d_sample = AdDecision(base_ts + 10, "rewarded", "ad:rewarded",
                          "end_of_level", rewarded=True, coin_reward=150)
    ev_sample = generate_ad_event(player, session, d_sample)
    print("  " + json.dumps(ev_sample, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. AdSimulator – state & rules
    # ============================================================
    print("\n[--- B. AD SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 8. try_session_ad() roll dice theo engagement
    # ------------------------------------------------------------------
    print("\n[8] Roll dice theo engagement (200 trial / segment)")
    rates = event_config.AD_PROBABILITY_PER_SESSION
    sim = AdSimulator(rng=random.Random(0))
    counts = {}
    for _ in range(200):
        for segment, expect_prob in rates.items():
            p = generate_players(count=1, seed=hash(segment) & 0xFFFFFFFF)[0]
            # Force engagement segment
            p.engagement_segment = segment
            d = sim.try_session_ad(p, base_ts)
            counts.setdefault(segment, 0)
            if d is not None:
                counts[segment] += 1

    for segment, expect_prob in rates.items():
        actual = counts.get(segment, 0) / 200.0
        print(f"  [INFO] {segment:>10}: actual={actual:.2f}, "
              f"expected~={expect_prob:.2f}")
        # Tolerance ±0.15 (binomial sampling noise)
        assert_check(abs(actual - expect_prob) < 0.15,
                     f"{segment}: |{actual:.2f} - {expect_prob:.2f}| < 0.15")

    # ------------------------------------------------------------------
    # 9. Hardcore roll nhiều hơn casual
    # ------------------------------------------------------------------
    print("\n[9] Hardcore roll nhiều hơn casual (n=1000)")
    rng_c = random.Random(11)
    rng_h = random.Random(11)
    sim_c = AdSimulator(rng=rng_c)
    sim_h = AdSimulator(rng=rng_h)
    count_c = 0
    count_h = 0
    for i in range(1000):
        pc = generate_players(count=1, seed=2000 + i)[0]
        pc.engagement_segment = "casual"
        if sim_c.try_session_ad(pc, base_ts + i) is not None:
            count_c += 1

        ph = generate_players(count=1, seed=2000 + i)[0]
        ph.engagement_segment = "hardcore"
        if sim_h.try_session_ad(ph, base_ts + i) is not None:
            count_h += 1

    print(f"  [INFO] Casual:  {count_c}/1000, Hardcore: {count_h}/1000")
    assert_check(count_h > count_c,
                 f"Hardcore ({count_h}) > Casual ({count_c})")

    # ------------------------------------------------------------------
    # 10. rewarded có coin_reward trong (50, 200)
    # ------------------------------------------------------------------
    print("\n[10] Rewarded ad: coin_reward trong (50, 200)")
    rng_r = random.Random(33)
    sim_r = AdSimulator(rng=rng_r)
    lo, hi = event_config.REWARDED_AD_COIN_REWARD_RANGE
    assert_check((lo, hi) == (50, 200),
                 f"REWARDED_AD_COIN_REWARD_RANGE = ({lo}, {hi})")
    # Lấy 1 rewarded ad
    found_rewarded = 0
    total_reward_range_ok = 0
    for i in range(200):
        p = generate_players(count=1, seed=3000 + i)[0]
        p.engagement_segment = "hardcore"  # sure to roll ad
        d = sim_r.try_session_ad(p, base_ts + i)
        if d is not None and d.rewarded:
            found_rewarded += 1
            if lo <= d.coin_reward <= hi:
                total_reward_range_ok += 1
    assert_check(found_rewarded > 0,
                 f"Tìm thấy {found_rewarded} rewarded ads trong 200 trials")
    if found_rewarded > 0:
        ratio = total_reward_range_ok / found_rewarded
        assert_check(ratio == 1.0,
                     f"100% rewarded có coin_reward trong range ({total_reward_range_ok}/{found_rewarded})")

    # ------------------------------------------------------------------
    # 11. eCPM đúng theo ad_type + country
    # ------------------------------------------------------------------
    print("\n[11] eCPM ước tính = avg(ad_type, country)")
    # Smoke: manual computation
    expected = (5.0 + 8.0) / 2.0  # rewarded (5) + US (8)
    actual = AdSimulator.compute_ecpm("rewarded", "US")
    assert_check(actual == expected,
                 f"compute_ecpm('rewarded', 'US') = {actual} (want {expected})")
    expected2 = (0.5 + 1.0) / 2.0  # banner (0.5) + VN (1.0)
    actual2 = AdSimulator.compute_ecpm("banner", "VN")
    assert_check(actual2 == expected2,
                 f"compute_ecpm('banner', 'VN') = {actual2} (want {expected2})")
    # Decision của player VN xem rewarded
    p_vn = generate_players(count=1, seed=99)[0]
    p_vn.engagement_segment = "hardcore"
    p_vn.country = "VN"
    rng_v = random.Random(0)
    sim_v = AdSimulator(rng=rng_v)
    d = sim_v.try_session_ad(p_vn, base_ts)
    if d is not None:
        if d.ad_type == "rewarded":
            want = (5.0 + 1.0) / 2.0
        else:
            want_ecpm_type = event_config.ECPM_BY_AD_TYPE[d.ad_type]
            want = (want_ecpm_type + 1.0) / 2.0
        assert_check(abs(d.eCPM_estimated - want) < 0.01,
                     f"VN rewarded: eCPM = {d.eCPM_estimated} ≈ {want}")

    # ------------------------------------------------------------------
    # 12. try_session_ads trả list
    # ------------------------------------------------------------------
    print("\n[12] try_session_ads trả list (có thể rỗng)")
    rng_s = random.Random(55)
    sim_s = AdSimulator(rng=rng_s)
    p_s = generate_players(count=1, seed=4000)[0]
    p_s.engagement_segment = "casual"
    ads = sim_s.try_session_ads(p_s, base_ts, n_max=5)
    assert_check(isinstance(ads, list),
                 f"try_session_ads returns list (got {type(ads).__name__})")
    print(f"  [INFO] Casual session: {len(ads)} ads (n_max=5)")
    # Casual có thể có 0 ads (prob=0.4)

    p_s2 = generate_players(count=1, seed=4001)[0]
    p_s2.engagement_segment = "hardcore"
    ads2 = sim_s.try_session_ads(p_s2, base_ts + 1, n_max=5)
    print(f"  [INFO] Hardcore session: {len(ads2)} ads")

    # ------------------------------------------------------------------
    # 13. Anti-spam: n_max giới hạn
    # ------------------------------------------------------------------
    print("\n[13] Anti-spam: n_max=3 dù hardcore")
    ads_bounded = sim_s.try_session_ads(p_s2, base_ts + 1, n_max=3)
    assert_check(len(ads_bounded) <= 3,
                 f"n_max=3: trả về {len(ads_bounded)} (<= 3)")

    # ------------------------------------------------------------------
    # 14. event_id match GA design spec format
    # ------------------------------------------------------------------
    print("\n[14] event_id match GA design spec format")
    for ad_type in ("rewarded", "interstitial", "banner"):
        d = AdDecision(base_ts, ad_type, f"ad:{ad_type}", "end_of_level",
                       rewarded=(ad_type == "rewarded"),
                       coin_reward=(150 if ad_type == "rewarded" else 0))
        ev = generate_ad_event(player, session, d)
        assert_check(ev["event_id"] == f"Ad:{ad_type}:end_of_level",
                     f"ad_type='{ad_type}' -> event_id='{ev['event_id']}'")

    # ============================================================
    # C. Rewarded + Resource liên kết (Bước 12 + Bước 10)
    # ============================================================
    print("\n[--- C. REWARDED → RESOURCE COUPLING ---]")

    # ------------------------------------------------------------------
    # 15. Rewarded → sinh 2 events (ad + resource source)
    # ------------------------------------------------------------------
    print("\n[15] Rewarded → CẶP events (ad + resource source)")
    p_r = generate_players(count=1, seed=99)[0]
    p_r.engagement_segment = "hardcore"
    p_r.coins = 1000

    # Chạy nhiều trial cho đến khi có rewarded
    found_pair = False
    for seed_offset in range(50):
        seed_val = 5000 + seed_offset
        rng_p = random.Random(seed_val)
        sim_p = AdSimulator(rng=rng_p)
        p_p = generate_players(count=1, seed=seed_val)[0]
        p_p.engagement_segment = "hardcore"
        p_p.coins = 1000
        d = sim_p.try_session_ad(p_p, base_ts)
        if d is not None and d.rewarded:
            # ad event
            ev_ad = generate_ad_event(p_p, session, d)
            # resource transaction
            tx = build_rewarded_resource_tx(d, EconomySimulator(rng_p))
            ev_res = generate_resource_event(p_p, session, tx)
            assert_check(ev_ad["category"] == "design",
                         "Rewarded cặp 1: design event (mapped từ ad)")
            assert_check(ev_res["category"] == "resource",
                         "Rewarded cặp 2: resource event")
            # Linkage: cùng client_ts
            assert_check(ev_ad["client_ts"] == ev_res["client_ts"],
                         "Ad + resource cùng client_ts")
            # Ad amount == resource amount
            assert_check(ev_ad["coin_reward"] == abs(ev_res["amount"]),
                         f"coin_reward={ev_ad['coin_reward']} match resource amount={abs(ev_res['amount'])}")
            found_pair = True
            break
    assert_check(found_pair,
                 "Tìm được ít nhất 1 rewarded ad trong 50 trials")

    # ------------------------------------------------------------------
    # 16. Resource event_id = 'Source:Coins:Ad:Rewarded'
    # ------------------------------------------------------------------
    print("\n[16] Resource event_id = 'Source:Coins:Ad:Rewarded'")
    p_x = generate_players(count=1, seed=88)[0]
    d_x = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                     rewarded=True, coin_reward=180)
    tx_x = build_rewarded_resource_tx(d_x, EconomySimulator(random.Random(0)))
    assert_check(tx_x.event_id == "Source:Coins:Ad:Rewarded",
                 f"event_id = '{tx_x.event_id}'")
    assert_check(tx_x.flow_type == "Source", "flow_type = 'Source'")
    assert_check(tx_x.virtual_currency == "Coins", "currency = 'Coins'")
    assert_check(tx_x.item_type == "Ad", "item_type = 'Ad'")
    assert_check(tx_x.item_id == "Rewarded", "item_id = 'Rewarded'")
    assert_check(tx_x.amount == 180, f"amount = {tx_x.amount} (= coin_reward)")

    # ------------------------------------------------------------------
    # 17. Coins balance += coin_reward sau khi economy.award()
    # ------------------------------------------------------------------
    print("\n[17] Balance += coin_reward khi economy.award()")
    p_e = generate_players(count=1, seed=77)[0]
    p_e.engagement_segment = "hardcore"
    p_e.coins = 500
    pre_coins = p_e.coins

    eco = EconomySimulator(rng=random.Random(0))
    d_e = AdDecision(base_ts, "rewarded", "ad:rewarded", "end_of_level",
                     rewarded=True, coin_reward=120)
    # ResourceTransaction từ build_rewarded_resource_tx
    tx_e = build_rewarded_resource_tx(d_e, eco)
    # Apply vào balance
    eco.award(p_e, "Coins", "Ad", "Rewarded", client_ts=base_ts,
              amount=d_e.coin_reward)
    post_coins = p_e.coins
    assert_check(post_coins == pre_coins + d_e.coin_reward,
                 f"Coins: {pre_coins} -> {post_coins} (+{d_e.coin_reward})")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 12 AD OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
