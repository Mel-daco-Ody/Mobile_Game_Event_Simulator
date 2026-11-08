"""Smoke test cho BƯỚC 13 – Design Event & Design Simulator.

Kiểm tra DesignGenerator (event payload):
    1. category = 'design'.
    2. Đầy đủ 11 common annotations.
    3. event_id thuộc DESIGN_EVENT_IDS cố định (12 ID).
    4. event_id KHÔNG chứa user_id/UUID/timestamp.
    5. value là int >= 0.
    6. custom_fields (nếu có) chỉ từ CUSTOM_FIELDS và giá trị hợp lệ.
    7. Raise ValueError nếu event_id không hợp lệ / value không phải int / custom field lạ.
    8. Tất cả 12 event_id trong DESIGN_EVENT_IDS đều pass.

Kiểm tra DesignSimulator:
    9. try_event roll dice theo engagement (200 trial / segment).
    10. pick_event_id() trả 1 trong 12 ID.
    11. value đúng range theo event_id (Kill:Boss damage 100-1000).
    12. Custom fields attach với probability ~0.30.
    13. try_events_for_session() giới hạn n_max.
    14. event_id KHÔNG bao giờ random number/UUID → 100% valid ID.

Chạy:
    cd prj && python generators/smoke_test_design.py
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

from config import event_config  # noqa: E402
from generators import (  # noqa: E402
    generate_design_event,
    generate_one_player,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.design import (  # noqa: E402
    DesignDecision,
    DesignSimulator,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 13: DESIGN EVENT & DESIGN SIMULATOR")
    print("=" * 60)

    player = generate_players(count=1, seed=42)[0]
    base_ts = int(time.time())
    session = generate_session(player, session_start_ts=base_ts)

    # ============================================================
    # A. DesignGenerator – event payload
    # ============================================================
    print("\n[--- A. DESIGN EVENT PAYLOAD ---]")

    # ------------------------------------------------------------------
    # 1. category = 'design'
    # ------------------------------------------------------------------
    print("\n[1] category = 'design'")
    decision = DesignDecision(
        client_ts=base_ts + 10,
        event_id="Gameplay:Kill:Boss",
        value=738,
        custom_fields={},
    )
    event = generate_design_event(player, session, decision)
    assert_check(event["category"] == "design",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations + design fields
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations + design fields")
    required = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
        "event_id", "value",
    }
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields")

    # ------------------------------------------------------------------
    # 3. event_id thuộc DESIGN_EVENT_IDS (12 ID)
    # ------------------------------------------------------------------
    print("\n[3] event_id thuộc DESIGN_EVENT_IDS cố định (12 ID)")
    assert_check(len(event_config.DESIGN_EVENT_IDS) == 12,
                 f"DESIGN_EVENT_IDS có đúng 12 ID: {len(event_config.DESIGN_EVENT_IDS)}")
    assert_check(event["event_id"] in event_config.DESIGN_EVENT_IDS,
                 f"event_id = '{event['event_id']}' trong tập cố định")

    # ------------------------------------------------------------------
    # 4. event_id KHÔNG chứa user_id/UUID/timestamp
    # ------------------------------------------------------------------
    print("\n[4] event_id KHÔNG chứa user_id/UUID/timestamp")
    eid = event["event_id"]
    assert_check(player.user_id not in eid,
                 f"event_id KHÔNG chứa user_id '{player.user_id}'")
    uuid_like = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        eid, flags=re.IGNORECASE,
    )
    assert_check(uuid_like is None, "event_id KHÔNG chứa UUID")
    assert_check(not re.search(r"\d{10,}", eid),
                 "event_id KHÔNG chứa timestamp")

    # ------------------------------------------------------------------
    # 5. value là int >= 0
    # ------------------------------------------------------------------
    print("\n[5] value là int >= 0")
    assert_check(isinstance(event["value"], int),
                 f"value là int (got {type(event['value']).__name__})")
    assert_check(event["value"] >= 0,
                 f"value >= 0 (got {event['value']})")
    assert_check(event["value"] == 738,
                 f"value = 738 (got {event['value']})")

    # ------------------------------------------------------------------
    # 6. custom_fields (nếu có) chỉ từ CUSTOM_FIELDS hợp lệ
    # ------------------------------------------------------------------
    print("\n[6] custom_fields (nếu có) chỉ từ CUSTOM_FIELDS")
    # Với custom fields
    decision_with_custom = DesignDecision(
        client_ts=base_ts + 20,
        event_id="Gameplay:Quest:Complete",
        value=300,
        custom_fields={"game_mode": "endless", "difficulty": "hard",
                        "ab_group": "B"},
    )
    ev_custom = generate_design_event(player, session, decision_with_custom)
    assert_check(ev_custom.get("game_mode") == "endless", "game_mode = endless")
    assert_check(ev_custom.get("difficulty") == "hard", "difficulty = hard")
    assert_check(ev_custom.get("ab_group") == "B", "ab_group = B")

    # Giá trị KHÔNG hợp lệ
    try:
        bad = DesignDecision(base_ts, "Gameplay:Quest:Complete", 100,
                             custom_fields={"game_mode": "INVALID"})
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với game_mode='INVALID'")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # Field name lạ
    try:
        bad = DesignDecision(base_ts, "Gameplay:Quest:Complete", 100,
                             custom_fields={"player_segment": "whale"})
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với field 'player_segment'")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ------------------------------------------------------------------
    # 7. ValueError nếu event_id / value / custom không hợp lệ
    # ------------------------------------------------------------------
    print("\n[7] ValueError cho input không hợp lệ")
    # event_id KHÔNG thuộc DESIGN_EVENT_IDS
    try:
        bad = DesignDecision(base_ts, "Gameplay:Kill:Dragon", 100)
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với event_id lạ")
    except ValueError as e:
        assert_check(True, f"ValueError (unknown event_id): {e}")

    # Dynamic ID có UUID
    try:
        uuid_id = "Gameplay:550e8400-e29b-41d4-a716-446655440000"
        bad = DesignDecision(base_ts, uuid_id, 100)
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với UUID trong event_id")
    except ValueError as e:
        assert_check(True, f"ValueError (UUID): {e}")

    # value KHÔNG phải int
    try:
        bad = DesignDecision(base_ts, "Gameplay:Kill:Zombie", "100")  # type: ignore[arg-type]
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với value='100' (str)")
    except ValueError as e:
        assert_check(True, f"ValueError (value str): {e}")

    # value âm
    try:
        bad = DesignDecision(base_ts, "Gameplay:Kill:Zombie", -10)
        generate_design_event(player, session, bad)
        raise AssertionError("Không raise với value<0")
    except ValueError as e:
        assert_check(True, f"ValueError (value<0): {e}")

    # ------------------------------------------------------------------
    # 8. Tất cả 12 event_id đều pass qua generator
    # ------------------------------------------------------------------
    print("\n[8] Tất cả 12 event_id đều pass")
    for eid in event_config.DESIGN_EVENT_IDS:
        d = DesignDecision(base_ts + 1, eid, 100, {})
        ev = generate_design_event(player, session, d)
        assert_check(ev["event_id"] == eid,
                     f"event_id '{eid}' pass qua generator")
        assert_check(ev["category"] == "design",
                     f"category=design cho '{eid}'")

    print("\n[Sample] Design event (Kill:Boss) payload:")
    d_sample = DesignDecision(
        base_ts + 10, "Gameplay:Kill:Boss", 738,
        custom_fields={"game_mode": "endless", "difficulty": "hard"},
    )
    ev_sample = generate_design_event(player, session, d_sample)
    print("  " + json.dumps(ev_sample, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. DesignSimulator – logic
    # ============================================================
    print("\n[--- B. DESIGN SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 9. try_event roll dice theo engagement
    # ------------------------------------------------------------------
    print("\n[9] Roll dice theo engagement (200 trial / segment)")
    rates = event_config.DESIGN_EVENT_PROBABILITY_PER_SESSION
    sim = DesignSimulator(rng=random.Random(0))
    counts = {}
    for _ in range(200):
        for segment, expect_prob in rates.items():
            p = generate_players(count=1, seed=hash(segment) & 0xFFFFFFFF)[0]
            p.engagement_segment = segment
            d = sim.try_event(p, base_ts)
            counts.setdefault(segment, 0)
            if d is not None:
                counts[segment] += 1

    for segment, expect_prob in rates.items():
        actual = counts.get(segment, 0) / 200.0
        print(f"  [INFO] {segment:>10}: actual={actual:.2f}, "
              f"expected~={expect_prob:.2f}")
        assert_check(abs(actual - expect_prob) < 0.15,
                     f"{segment}: |{actual:.2f} - {expect_prob:.2f}| < 0.15")

    # ------------------------------------------------------------------
    # 10. pick_event_id() trả 1 trong 12 ID
    # ------------------------------------------------------------------
    print("\n[10] pick_event_id() trả 1 trong 12 ID")
    sim_p = DesignSimulator(rng=random.Random(42))
    seen = set()
    for _ in range(200):
        eid = sim_p.pick_event_id()
        assert_check(eid in event_config.DESIGN_EVENT_IDS,
                     f"pick_event_id trả '{eid}' thuộc DESIGN_EVENT_IDS")
        seen.add(eid)
    assert_check(len(seen) >= 5,
                 f"Variety >= 5 trong 200 trials: {len(seen)} ID")

    # ------------------------------------------------------------------
    # 11. value đúng range theo event_id
    # ------------------------------------------------------------------
    print("\n[11] value đúng range theo event_id")
    sim_v = DesignSimulator(rng=random.Random(11))
    for _ in range(50):
        eid = sim_v.pick_event_id()
        d = DesignDecision(base_ts, eid, sim_v._sample_value(eid), {})
        if eid in event_config.DESIGN_VALUE_RANGES:
            lo, hi, _ = event_config.DESIGN_VALUE_RANGES[eid]
            assert_check(lo <= d.value <= hi,
                         f"{eid}: value={d.value} ∈ [{lo}, {hi}]")
        assert_check(isinstance(d.value, int),
                     f"{eid}: value là int")

    # ------------------------------------------------------------------
    # 12. Custom fields attach với probability ~0.30
    # ------------------------------------------------------------------
    print("\n[12] Custom fields attach với probability ~0.30")
    sim_c = DesignSimulator(rng=random.Random(0))
    p = generate_players(count=1, seed=42)[0]
    p.engagement_segment = "hardcore"  # sure to roll
    none_count = 0
    some_count = 0
    total_attachments = 0
    N = 500
    for _ in range(N):
        d = sim_c.try_event(p, base_ts)
        if d is not None:
            if not d.custom_fields:
                none_count += 1
            else:
                some_count += 1
                total_attachments += len(d.custom_fields)
    # Expect ~30% attach per field × 3 fields ≈ 0.9 attachment per event
    print(f"  [INFO] {some_count}/{N} events có custom fields, "
          f"avg attachments per event = {total_attachments / N:.2f}")
    # Cho events có attachments
    if some_count > 0:
        avg_per_event = total_attachments / N
        # Expected: 3 fields × 0.30 = 0.9 per event
        assert_check(0.5 < avg_per_event < 1.3,
                     f"avg {avg_per_event:.2f} trong khoảng hợp lý (0.5-1.3)")

    # Verify giá trị custom fields chỉ từ tập giới hạn
    for _ in range(100):
        d = sim_c.try_event(p, base_ts)
        if d is not None:
            for key, val in d.custom_fields.items():
                assert_check(key in event_config.CUSTOM_FIELDS,
                             f"custom field key '{key}' hợp lệ")
                assert_check(val in event_config.CUSTOM_FIELDS[key],
                             f"custom field value '{val}' trong {event_config.CUSTOM_FIELDS[key]}")

    # ------------------------------------------------------------------
    # 13. try_events_for_session() giới hạn n_max
    # ------------------------------------------------------------------
    print("\n[13] try_events_for_session() giới hạn n_max")
    sim_s = DesignSimulator(rng=random.Random(55))
    p_h = generate_players(count=1, seed=4001)[0]
    p_h.engagement_segment = "hardcore"
    ads = sim_s.try_events_for_session(p_h, base_ts, n_max=4)
    assert_check(isinstance(ads, list),
                 f"returns list (got {type(ads).__name__})")
    assert_check(len(ads) <= 4,
                 f"n_max=4: trả về {len(ads)} (<= 4)")

    # ------------------------------------------------------------------
    # 14. event_id 100% thuộc DESIGN_EVENT_IDS (không dynamic)
    # ------------------------------------------------------------------
    print("\n[14] 100% event_id thuộc DESIGN_EVENT_IDS")
    sim_e = DesignSimulator(rng=random.Random(0))
    valid_count = 0
    total_count = 0
    for _ in range(500):
        d = sim_e.try_event(p, base_ts)
        if d is not None:
            total_count += 1
            if d.event_id in event_config.DESIGN_EVENT_IDS:
                valid_count += 1
    assert_check(valid_count == total_count,
                 f"100% event_id thuộc DESIGN_EVENT_IDS ({valid_count}/{total_count})")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 13 DESIGN OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
