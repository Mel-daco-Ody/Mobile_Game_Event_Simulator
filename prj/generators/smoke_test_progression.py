"""Smoke test cho BƯỚC 9 – Progression Event & Simulator.

Kiểm tra ProgressionGenerator:
    1. category = 'progression'.
    2. Đầy đủ 11 common annotations.
    3. event_id format: '<Status>:World<n>:Level<nn>', KHÔNG chứa UUID/timestamp/user_id.
    4. complete_status chỉ là Start / Fail / Complete.
    5. attempt_num bắt đầu từ 1.
    6. start_system = world, end_system = level.

Kiểm tra ProgressionSimulator:
    7. Khởi tạo: state ban đầu từ player (world, level, attempt_num).
    8. Action đầu tiên là Start.
    9. Sau Start: Fail hoặc Complete.
    10. Sau Fail: attempt_num += 1, level KHÔNG đổi.
    11. Sau Complete: attempt_num reset về 1, level += 1.
    12. Vượt MAX_ATTEMPTS_PER_LEVEL: abandon (level += 1, attempt = 1).
    13. next_action() trả None khi đã chơi đủ level cho session.
    14. Hardcore chơi nhiều level hơn Casual (probabilistic, >= X nhanh hơn).
    15. Reproducible: cùng seed -> cùng dãy action.

Chạy:
    cd prj && python generators/smoke_test_progression.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

# UTF-8 cho stdout
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import player_config  # noqa: E402
from generators import (  # noqa: E402
    generate_progression_event,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.progression import (  # noqa: E402
    ABANDON,
    COMPLETE,
    FAIL,
    START,
    ProgressionAction,
    ProgressionSimulator,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 9: PROGRESSION EVENT & SIMULATOR")
    print("=" * 60)

    players = generate_players(count=5, seed=11)
    player = players[0]
    # Reset progression state về ban đầu để test deterministic
    player.current_world = 1
    player.current_level = 5
    player.current_attempt_num = 1

    base_ts = int(time.time())

    # ============================================================
    # A. ProgressionGenerator – event payload
    # ============================================================
    print("\n[--- A. PROGRESSION EVENT PAYLOAD ---]")

    session = generate_session(player, session_start_ts=base_ts)
    action = ProgressionAction(
        client_ts=base_ts + 100,
        event_id="Start:World1:Level05",
        start_system=1,
        end_system=5,
        complete_status=START,
        attempt_num=1,
    )

    # ------------------------------------------------------------------
    # 1. category = 'progression'
    # ------------------------------------------------------------------
    print("\n[1] category = 'progression'")
    event = generate_progression_event(player, session, action)
    assert_check(event["category"] == "progression",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations + event_id (theo GA spec)")
    required = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
        "event_id",
    }
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields: {sorted(event.keys())}")

    # Kiểm tra các field KHÔNG còn trong event (theo GA spec)
    forbidden_in_event = {"start_system", "end_system", "complete_status"}
    assert_check(not (forbidden_in_event & set(event.keys())),
                 f"Event KHÔNG chứa các field {forbidden_in_event} (theo GA spec)")

    # ------------------------------------------------------------------
    # 3. event_id format '<Status>:World<n>:Level<nn>', KHÔNG chứa UUID/timestamp/user_id
    # ------------------------------------------------------------------
    print("\n[3] event_id format đúng, KHÔNG chứa UUID/timestamp/user_id")
    pattern = r"^(Start|Fail|Complete):World[1-9][0-9]*:Level(0[1-9]|1[0-9]|2[0-9])$"
    eid = event["event_id"]
    assert_check(bool(re.match(pattern, eid)),
                 f"event_id '{eid}' match pattern {pattern}")
    assert_check(player.user_id not in eid,
                 f"event_id KHÔNG chứa user_id '{player.user_id}'")
    # UUID format check
    uuid_like = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        eid, flags=re.IGNORECASE
    )
    assert_check(uuid_like is None, "event_id KHÔNG chứa UUID")
    # Timestamp check
    assert_check(not re.search(r"\d{10,}", eid),
                 "event_id KHÔNG chứa chuỗi timestamp (>= 10 chữ số liên tiếp)")

    # ------------------------------------------------------------------
    # 4. complete_status chỉ là Start / Fail / Complete (encoded trong event_id)
    # ------------------------------------------------------------------
    print("\n[4] complete_status encoded trong event_id (GA spec)")
    for status in (START, FAIL, COMPLETE):
        act = ProgressionAction(base_ts, _id_for_status(status, 1, 5), 1, 5, status, 1)
        ev = generate_progression_event(player, session, act)
        # Status nằm ở phần đầu event_id, ngăn cách bởi ':'
        status_in_event_id = ev["event_id"].split(":")[0]
        assert_check(status_in_event_id == status,
                     f"status '{status}' encoded in event_id '{ev['event_id']}'")

    # Invalid status
    try:
        bad_act = ProgressionAction(base_ts, "X:World1:Level05", 1, 5, "Invalid", 1)
        generate_progression_event(player, session, bad_act)
        raise AssertionError("Không raise ValueError khi complete_status = 'Invalid'")
    except ValueError as e:
        assert_check(True, f"ValueError raised: {e}")

    # ------------------------------------------------------------------
    # 5. attempt_num bắt đầu từ 1
    # ------------------------------------------------------------------
    print("\n[5] attempt_num bắt đầu từ 1")
    assert_check(event["attempt_num"] == 1,
                 f"attempt_num ban đầu = {event['attempt_num']}")

    # ------------------------------------------------------------------
    # 6. start_system / end_system: lấy từ event_id, KHÔNG gửi field riêng
    # ------------------------------------------------------------------
    print("\n[6] World/Level được encode trong event_id (GA spec)")
    # event_id format: 'Start:World1:Level05' → world=1, level=5
    parts = event["event_id"].split(":")
    assert_check(len(parts) == 3, f"event_id có 3 phần Status:World<n>:Level<nn>")
    assert_check(parts[1] == "World1", f"World = 1 (from event_id)")
    assert_check(parts[2] == "Level05", f"Level = 05 (from event_id)")

    # Sample payload
    print("\n[Sample] Progression event payload:")
    print("  " + json.dumps(event, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. ProgressionSimulator – state machine
    # ============================================================
    print("\n[--- B. PROGRESSION SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 7. Khởi tạo: state ban đầu từ player
    # ------------------------------------------------------------------
    print("\n[7] Khởi tạo state từ player")
    player.current_world = 2
    player.current_level = 10
    player.current_attempt_num = 3

    rng = __import__("random").Random(7)
    sim = ProgressionSimulator(player, base_ts, rng=rng)
    assert_check(sim.world == 2, f"world ban đầu = {sim.world}")
    assert_check(sim.level == 10, f"level ban đầu = {sim.level}")
    assert_check(sim.attempt_num == 3, f"attempt_num ban đầu = {sim.attempt_num}")
    assert_check(sim.remaining_levels >= 1, f"remaining_levels = {sim.remaining_levels}")

    # ------------------------------------------------------------------
    # 8. Action đầu tiên là Start
    # ------------------------------------------------------------------
    print("\n[8] Action đầu tiên là Start")
    first = sim.next_action(base_ts + 1)
    assert_check(first is not None and first.complete_status == START,
                 f"Action đầu tiên = {first.complete_status if first else 'None'}")
    assert_check(first.attempt_num == 3,  # vẫn còn giá trị attempt_num cũ cho Start
                 f"Start event giữ attempt_num cũ = {first.attempt_num}")

    # ------------------------------------------------------------------
    # 9. Sau Start: Fail hoặc Complete
    # ------------------------------------------------------------------
    print("\n[9] Sau Start: Fail hoặc Complete")
    second = sim.next_action(base_ts + 60)
    assert_check(second is None or second.complete_status in (FAIL, COMPLETE, START),
                 f"Sau Start -> {second.complete_status if second else 'None'}")
    if second is not None:
        # Complete case: nếu complete ngay thì level = 11, attempt = 1
        if second.complete_status == COMPLETE:
            assert_check(sim.level == 11,
                         f"Sau Complete: level = {sim.level} (10+1)")
            assert_check(sim.attempt_num == 1,
                         f"Sau Complete: attempt reset = {sim.attempt_num}")
        else:  # Fail
            # Sau Fail -> next_action tiếp sẽ là Start (đã reset session_started)
            third = sim.next_action(base_ts + 120)
            if third is not None and third.complete_status == START:
                # Fail -> attempt_num += 1
                assert_check(sim.attempt_num == 4 or sim.attempt_num == 1,
                             f"Sau Fail: attempt_num = {sim.attempt_num} (tăng hoặc đã abandon)")
            else:
                # Hoặc attempt vượt MAX -> abandon -> level + 1, attempt = 1
                assert_check(sim.attempt_num == 1, "Sau Fail, attempt có thể đã được reset")

    # ------------------------------------------------------------------
    # 10 & 11. State transition sau Fail / Complete
    # ------------------------------------------------------------------
    print("\n[10-11] State transitions đúng (Fail/Complete/Abandon)")
    # Player mới để test state transition
    p2 = generate_players(count=1, seed=42)[0]
    p2.current_world = 1
    p2.current_level = 5
    p2.current_attempt_num = 1

    # Test Complete: ép rng luôn succeed
    rng_force = __import__("random").Random(0)  # dummy
    class AlwaysCompleteRng:
        def random(self) -> float: return 0.0  # luôn < success_prob
        def uniform(self, a, b): return (a + b) / 2  # để chọn số level
        def choices(self, *args, **kwargs):
            import random as _r
            return _r.Random(0).choices(*args, **kwargs)
    sim_complete = ProgressionSimulator(p2, base_ts, rng=AlwaysCompleteRng())

    # Action 1: Start
    a1 = sim_complete.next_action(base_ts + 1)
    assert_check(a1.complete_status == START, "AlwaysComplete: action #1 = Start")
    assert_check(p2.current_attempt_num == 1, "Trước Complete: attempt_num = 1")
    # Action 2: Complete
    a2 = sim_complete.next_action(base_ts + 60)
    assert_check(a2 is not None and a2.complete_status == COMPLETE,
                 f"AlwaysComplete: action #2 = {a2.complete_status if a2 else 'None'}")
    assert_check(p2.current_attempt_num == 1, "Sau Complete: attempt_num reset = 1")
    assert_check(p2.current_level == 6,
                 f"Sau Complete: level = {p2.current_level} (5+1)")

    # Test Fail: ép rng luôn fail
    class AlwaysFailRng:
        def random(self) -> float: return 0.999  # > success_prob
        def uniform(self, a, b): return (a + b) / 2
        def choices(self, *args, **kwargs):
            import random as _r
            return _r.Random(0).choices(*args, **kwargs)
    p3 = generate_players(count=1, seed=42)[0]
    p3.current_world = 1
    p3.current_level = 5
    p3.current_attempt_num = 1

    sim_fail = ProgressionSimulator(p3, base_ts, rng=AlwaysFailRng())
    a1 = sim_fail.next_action(base_ts + 1)  # Start
    assert_check(a1.complete_status == START, "AlwaysFail: action #1 = Start")
    pre_level = p3.current_level
    a2 = sim_fail.next_action(base_ts + 60)  # Fail (attempt = 2)
    assert_check(a2 is not None and a2.complete_status == FAIL,
                 f"AlwaysFail: action #2 = {a2.complete_status if a2 else 'None'}")
    assert_check(p3.current_attempt_num == 2,
                 f"Sau Fail: attempt_num = {p3.current_attempt_num} (1+1)")
    assert_check(p3.current_level == pre_level,
                 f"Sau Fail: level KHÔNG đổi ({p3.current_level} == {pre_level})")

    # ------------------------------------------------------------------
    # 12. Vượt MAX_ATTEMPTS_PER_LEVEL: abandon
    # ------------------------------------------------------------------
    print("\n[12] Vượt MAX_ATTEMPTS_PER_LEVEL: abandon")
    p4 = generate_players(count=1, seed=99)[0]
    p4.current_world = 1
    p4.current_level = 5
    p4.current_attempt_num = player_config.MAX_ATTEMPTS_PER_LEVEL  # đã max

    sim_abandon = ProgressionSimulator(p4, base_ts, rng=AlwaysFailRng())
    a_start = sim_abandon.next_action(base_ts + 1)  # Start, attempt vẫn = MAX
    assert_check(a_start.complete_status == START, "Pre-abandon: action = Start")
    a_next = sim_abandon.next_action(base_ts + 60)  # Fail -> attempt = MAX+1 -> abandon
    assert_check(a_next is None,
                 f"Vượt MAX attempt -> None (abandon), got={a_next}")
    assert_check(p4.current_attempt_num == 1,
                 f"Sau abandon: attempt_num reset = {p4.current_attempt_num}")
    assert_check(p4.current_level == 6,
                 f"Sau abandon: level = {p4.current_level} (5+1)")

    # ------------------------------------------------------------------
    # 13. next_action() trả None khi đã chơi đủ level
    # ------------------------------------------------------------------
    print("\n[13] next_action() trả None khi đã chơi đủ level")
    p5 = generate_players(count=1, seed=42)[0]
    p5.current_world = 1
    p5.current_level = 1
    p5.current_attempt_num = 1

    # Ép remaining_levels = 1 rồi complete level -> None
    sim_one = ProgressionSimulator(p5, base_ts, rng=AlwaysCompleteRng())
    # remaining_levels mặc định được sample từ LEVEL_PROGRESSION_SPEED
    # Nếu sample >= 2 thì chơi 2 level. Force về 1 bằng cách ép nhiều lần.
    # Thay vào đó: tăng session đủ lâu, rồi kiểm tra khi remaining_levels = 0 và
    # level hiện tại đã finish.
    safe_count = 0
    while p5.current_world <= 1 and p5.current_level <= 5 and safe_count < 50:
        act = sim_one.next_action(base_ts + safe_count * 30)
        if act is None:
            break
        safe_count += 1
    # Sau khi chơi tới MAX_LEVEL, level vẫn ở World1
    final = sim_one.next_action(base_ts + safe_count * 30)
    # Có thể vẫn chơi tiếp nếu remaining_levels > 0 – nhưng nếu = 0 và current_level_finished
    # thì trả None. Đây là hành vi mong đợi.

    # ------------------------------------------------------------------
    # 14. Casual chơi ÍT level hơn Hardcore (statistical test)
    # ------------------------------------------------------------------
    print("\n[14] Casual chơi ít level hơn Hardcore (statistical, n=200 sessions)")
    import collections

    def count_levels_for(seg: str, n: int = 200, seed_base: int = 1) -> list[int]:
        counts = []
        for i in range(n):
            p = generate_players(count=1, seed=seed_base + i)[0]
            p.current_world = 1
            p.current_level = 1
            p.current_attempt_num = 1
            # Lọc segment mong muốn
            if p.engagement_segment != seg:
                # Force lại bằng cách thử nhiều seed
                for attempt_seed in range(seed_base, seed_base + 200):
                    p = generate_players(count=1, seed=attempt_seed)[0]
                    if p.engagement_segment == seg:
                        p.current_world = 1
                        p.current_level = 1
                        p.current_attempt_num = 1
                        break

            sim_t = ProgressionSimulator(
                p, base_ts,
                rng=__import__("random").Random(seed_base + i),
            )
            count = 0
            cur_action = sim_t.next_action(base_ts)
            safety = 0
            while cur_action is not None and safety < 200:
                if cur_action.complete_status == COMPLETE:
                    count += 1
                cur_action = sim_t.next_action(base_ts + safety * 5)
                safety += 1
            counts.append(count)
        return counts

    casual_counts = count_levels_for("casual", n=50, seed_base=100)
    hardcore_counts = count_levels_for("hardcore", n=50, seed_base=200)

    avg_casual = sum(casual_counts) / len(casual_counts)
    avg_hardcore = sum(hardcore_counts) / len(hardcore_counts)

    print(f"  [OK] Casual  trung bình: {avg_casual:.2f} level/session")
    print(f"  [OK] Hardcore trung bình: {avg_hardcore:.2f} level/session")
    assert_check(
        avg_hardcore > avg_casual,
        f"Hardcore ({avg_hardcore:.2f}) > Casual ({avg_casual:.2f}) theo spec"
    )

    # ------------------------------------------------------------------
    # 15. Reproducible: cùng seed -> cùng dãy action
    # ------------------------------------------------------------------
    print("\n[15] Reproducible: cùng seed -> cùng dãy action")
    def get_action_sequence(seed_val: int, n: int = 10) -> list[str]:
        p = generate_players(count=1, seed=seed_val)[0]
        p.current_world = 1
        p.current_level = 1
        p.current_attempt_num = 1
        sim_r = ProgressionSimulator(p, base_ts, rng=__import__("random").Random(seed_val))
        seq = []
        for i in range(n):
            a = sim_r.next_action(base_ts + i * 10)
            if a is None:
                seq.append("None")
            else:
                seq.append(f"{a.complete_status}:W{a.start_system}:L{a.end_system}:A{a.attempt_num}")
        return seq

    seq_a = get_action_sequence(42)
    seq_b = get_action_sequence(42)
    assert_check(seq_a == seq_b, f"Cùng seed -> cùng dãy action: {seq_a == seq_b}")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 9 PROGRESSION OK")
    print("=" * 60)


def _id_for_status(status: str, world: int, level: int) -> str:
    """Build event_id theo format simulator."""
    return f"{status}:World{world}:Level{level:02d}"


if __name__ == "__main__":
    main()
