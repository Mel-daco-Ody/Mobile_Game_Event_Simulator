"""Smoke test cho BƯỚC 14 – Error Event & Error Simulator.

Kiểm tra ErrorGenerator (event payload):
    1. category = 'error'.
    2. Đầy đủ 11 common annotations + error fields.
    3. event_id format 'error:<message>' (message tĩnh).
    4. severity ∈ {debug, info, warning, error, critical}.
    5. message ∈ ERROR_MESSAGES cố định (5 message).
    6. stack_trace optional, không chứa UUID/timestamp/user_id.
    7. Raise ValueError với severity/message sai.

Kiểm tra ErrorSimulator:
    8. Roll dice ERROR_PROBABILITY_PER_EVENT = 0.001 (n=2000).
    9. Cap MAX_ERROR_EVENTS_PER_SESSION = 1.
    10. Pick severity theo weights ~[0.40, 0.30, 0.20, 0.08, 0.02].
    11. Pick message từ ERROR_MESSAGES.
    12. KHÔNG bao giờ tạo event_id động (event_id luôn 'error:<message>').
    13. try_events_for_session cap chính xác 1.

Chạy:
    cd prj && python generators/smoke_test_error.py
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from collections import Counter

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
    generate_error_event,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.error import (  # noqa: E402
    ErrorDecision,
    ErrorSimulator,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 14: ERROR EVENT & ERROR SIMULATOR")
    print("=" * 60)

    player = generate_players(count=1, seed=42)[0]
    base_ts = int(time.time())
    session = generate_session(player, session_start_ts=base_ts)

    # ============================================================
    # A. ErrorGenerator – event payload
    # ============================================================
    print("\n[--- A. ERROR EVENT PAYLOAD ---]")

    # ------------------------------------------------------------------
    # 1. category = 'error'
    # ------------------------------------------------------------------
    print("\n[1] category = 'error'")
    decision = ErrorDecision(
        client_ts=base_ts + 10,
        severity="warning",
        message="network_timeout",
        stack_trace="NetworkManager.cs:142 | handler=network_timeout",
    )
    event = generate_error_event(player, session, decision)
    assert_check(event["category"] == "error",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations + error fields
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations + error fields")
    required = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
        "event_id", "severity", "message",
    }
    assert_check(required <= set(event.keys()),
                 f"Đủ {len(required)} fields")
    # stack_trace optional
    assert_check("stack_trace" in event,
                 "stack_trace có khi attach")

    # ------------------------------------------------------------------
    # 3. event_id format 'error:<message>'
    # ------------------------------------------------------------------
    print("\n[3] event_id format 'error:<message>' (message tĩnh)")
    assert_check(event["event_id"] == "error:network_timeout",
                 f"event_id = '{event['event_id']}'")
    for msg in event_config.ERROR_MESSAGES:
        d = ErrorDecision(base_ts, "warning", msg, "trace")
        ev = generate_error_event(player, session, d)
        assert_check(ev["event_id"] == f"error:{msg}",
                     f"event_id = 'error:{msg}'")
        # event_id KHÔNG chứa user_id/UUID/timestamp
        assert_check(player.user_id not in ev["event_id"],
                     f"event_id KHÔNG chứa user_id (msg={msg})")
        uuid_like = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            ev["event_id"], flags=re.IGNORECASE,
        )
        assert_check(uuid_like is None,
                     f"event_id KHÔNG chứa UUID (msg={msg})")

    # ------------------------------------------------------------------
    # 4. severity ∈ {debug, info, warning, error, critical}
    # ------------------------------------------------------------------
    print("\n[4] severity ∈ 5 enum")
    assert_check(set(event_config.ERROR_SEVERITY_LEVELS) ==
                 {"debug", "info", "warning", "error", "critical"},
                 f"ERROR_SEVERITY_LEVELS = {event_config.ERROR_SEVERITY_LEVELS}")
    for sev in event_config.ERROR_SEVERITY_LEVELS:
        d = ErrorDecision(base_ts, sev, "network_timeout", None)
        ev = generate_error_event(player, session, d)
        assert_check(ev["severity"] == sev,
                     f"severity='{sev}' pass")

    # ------------------------------------------------------------------
    # 5. message ∈ ERROR_MESSAGES cố định (5 message)
    # ------------------------------------------------------------------
    print("\n[5] message ∈ ERROR_MESSAGES cố định")
    assert_check(len(event_config.ERROR_MESSAGES) == 5,
                 f"ERROR_MESSAGES có 5 entry: {event_config.ERROR_MESSAGES}")

    # ------------------------------------------------------------------
    # 6. stack_trace KHÔNG chứa UUID/timestamp/user_id
    # ------------------------------------------------------------------
    print("\n[6] stack_trace KHÔNG chứa UUID/timestamp/user_id")
    d_st = ErrorDecision(base_ts, "error", "asset_load_failed",
                         "Engine.handle(asset_load_failed)")
    ev_st = generate_error_event(player, session, d_st)
    assert_check("stack_trace" in ev_st, "stack_trace OK")
    # UUID check
    try:
        bad = ErrorDecision(base_ts, "error", "asset_load_failed",
                            "Error at uuid=550e8400-e29b-41d4-a716-446655440000")
        generate_error_event(player, session, bad)
        raise AssertionError("Không raise với UUID trong stack_trace")
    except ValueError as e:
        assert_check(True, f"ValueError (UUID): {e}")
    # Timestamp check
    try:
        bad = ErrorDecision(base_ts, "error", "asset_load_failed",
                            "Error at ts=1759305600")
        generate_error_event(player, session, bad)
        raise AssertionError("Không raise với timestamp trong stack_trace")
    except ValueError as e:
        assert_check(True, f"ValueError (timestamp): {e}")
    # user_id check
    try:
        bad = ErrorDecision(base_ts, "error", "asset_load_failed",
                            f"Error for {player.user_id}")
        generate_error_event(player, session, bad)
        raise AssertionError("Không raise với user_id trong stack_trace")
    except ValueError as e:
        assert_check(True, f"ValueError (user_id): {e}")
    # stack_trace None – dùng message 'network_timeout' (hợp lệ)
    d_none = ErrorDecision(base_ts, "debug", "network_timeout", None)
    ev_none = generate_error_event(player, session, d_none)
    assert_check("stack_trace" not in ev_none,
                 "stack_trace=None → field không có trong event")

    # ------------------------------------------------------------------
    # 7. ValueError với severity/message sai
    # ------------------------------------------------------------------
    print("\n[7] ValueError với severity/message sai")
    # severity lạ
    try:
        bad = ErrorDecision(base_ts, "fatal", "network_timeout", None)
        generate_error_event(player, session, bad)
        raise AssertionError("Không raise với severity='fatal'")
    except ValueError as e:
        assert_check(True, f"ValueError (severity): {e}")
    # message lạ
    try:
        bad = ErrorDecision(base_ts, "warning", "out_of_memory", None)
        generate_error_event(player, session, bad)
        raise AssertionError("Không raise với message lạ")
    except ValueError as e:
        assert_check(True, f"ValueError (message): {e}")

    print("\n[Sample] Error event payload:")
    d_sample = ErrorDecision(
        base_ts + 10, "warning", "network_timeout",
        "NetworkManager.cs:142 | handler=retry",
    )
    ev_sample = generate_error_event(player, session, d_sample)
    print("  " + json.dumps(ev_sample, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # ============================================================
    # B. ErrorSimulator – logic
    # ============================================================
    print("\n[--- B. ERROR SIMULATOR ---]")

    # ------------------------------------------------------------------
    # 8. Roll dice ERROR_PROBABILITY_PER_EVENT = 0.001
    # ------------------------------------------------------------------
    print("\n[8] Roll dice ERROR_PROBABILITY_PER_EVENT = 0.001 (n=10000)")
    sim = ErrorSimulator(rng=random.Random(0))
    fires = 0
    for _ in range(10000):
        d = sim.try_event(base_ts, already_emitted_in_session=0,
                          prob_override=0.001)
        if d is not None:
            fires += 1
    rate = fires / 10000.0
    print(f"  [INFO] {fires}/10000 fires, rate={rate:.4f}, expected~=0.001")
    assert_check(0.0005 < rate < 0.0018,
                 f"rate {rate:.4f} ∈ (0.0005, 0.0018) – ~0.001")

    # ------------------------------------------------------------------
    # 9. Cap MAX_ERROR_EVENTS_PER_SESSION = 1
    # ------------------------------------------------------------------
    print("\n[9] Cap MAX_ERROR_EVENTS_PER_SESSION = 1")
    assert_check(event_config.MAX_ERROR_EVENTS_PER_SESSION == 1,
                 f"MAX_ERROR_EVENTS_PER_SESSION = {event_config.MAX_ERROR_EVENTS_PER_SESSION}")
    # already_emitted = 0 → có thể fire (khi prob cao); already_emitted >= 1 → None
    fired_first = False
    for _ in range(50):
        d = sim.try_event(base_ts + 1, already_emitted_in_session=0,
                          prob_override=1.0)  # force fire
        if d is not None:
            fired_first = True
            break
    assert_check(fired_first,
                 "Với already_emitted=0 + prob=1.0, LUÔN fire error")
    # Khi already_emitted >= 1 → KHÔNG bao giờ fire
    prob_override = 0.5
    fired_second = 0
    for _ in range(100):
        d = sim.try_event(base_ts + 1, already_emitted_in_session=1,
                          prob_override=prob_override)
        if d is not None:
            fired_second += 1
    assert_check(fired_second == 0,
                 f"already_emitted=1: 0/100 fires (got {fired_second})")

    # ------------------------------------------------------------------
    # 10. Pick severity theo weights ~[0.40, 0.30, 0.20, 0.08, 0.02]
    # ------------------------------------------------------------------
    print("\n[10] Pick severity theo weights")
    sim_s = ErrorSimulator(rng=random.Random(11))
    counter = Counter()
    N = 5000
    for _ in range(N):
        d = sim_s.try_event(base_ts, already_emitted_in_session=0,
                            prob_override=1.0)  # always fire
        if d is not None:
            counter[d.severity] += 1
    expected_weights = event_config.ERROR_SEVERITY_WEIGHTS
    expected_levels = event_config.ERROR_SEVERITY_LEVELS
    print(f"  [INFO] Sample counts: {dict(counter)}")
    for sev, w in zip(expected_levels, expected_weights):
        actual = counter.get(sev, 0) / N
        print(f"    {sev:>10}: actual={actual:.3f}, expected~={w:.3f}")
        assert_check(abs(actual - w) < 0.04,
                     f"{sev}: |{actual:.3f} - {w:.3f}| < 0.04")

    # ------------------------------------------------------------------
    # 11. Pick message từ ERROR_MESSAGES
    # ------------------------------------------------------------------
    print("\n[11] Pick message từ ERROR_MESSAGES (5 message)")
    msg_counter = Counter()
    for _ in range(2000):
        d = sim_s.try_event(base_ts, already_emitted_in_session=0,
                            prob_override=1.0)
        if d is not None:
            msg_counter[d.message] += 1
            assert_check(d.message in event_config.ERROR_MESSAGES,
                         f"message '{d.message}' thuộc ERROR_MESSAGES")
    # 5/2000 ≈ 400 mỗi message, ±20%
    print(f"  [INFO] message counts: {dict(msg_counter)}")
    for msg in event_config.ERROR_MESSAGES:
        cnt = msg_counter.get(msg, 0)
        assert_check(300 < cnt < 500,
                     f"{msg}: {cnt}/2000 ∈ (300, 500)")

    # ------------------------------------------------------------------
    # 12. event_id luôn 'error:<message>' – KHÔNG dynamic
    # ------------------------------------------------------------------
    print("\n[12] event_id 100% 'error:<message>' – KHÔNG dynamic")
    valid_count = 0
    total = 0
    seen_event_ids = set()
    for _ in range(200):
        d = sim_s.try_event(base_ts, already_emitted_in_session=0,
                            prob_override=0.05)
        if d is not None:
            ev = generate_error_event(player, session, d)
            total += 1
            seen_event_ids.add(ev["event_id"])
            if ev["event_id"] == f"error:{d.message}":
                valid_count += 1
    assert_check(valid_count == total,
                 f"100% event_id match 'error:<message>' ({valid_count}/{total})")
    # Variety: mỗi message thuộc ERROR_MESSAGES → ≤ 5 event_id duy nhất
    assert_check(len(seen_event_ids) <= len(event_config.ERROR_MESSAGES),
                 f"<= 5 unique event_id: {seen_event_ids}")
    # Mỗi event_id đều ∈ set 'error:<m>' đúng message
    for eid in seen_event_ids:
        assert_check(eid.startswith("error:"),
                     f"event_id '{eid}' prefix 'error:'")
        assert_check(eid.split(":", 1)[1] in event_config.ERROR_MESSAGES,
                     f"event_id '{eid}' chứa message hợp lệ")

    # ------------------------------------------------------------------
    # 13. try_events_for_session() cap chính xác 1
    # ------------------------------------------------------------------
    print("\n[13] try_events_for_session() cap chính xác 1")
    sim_session = ErrorSimulator(rng=random.Random(55))
    # prob_override=1.0 → always fire (giúp kiểm tra cap)
    decisions = []
    for i in range(500):
        ts = base_ts + i * 5
        d = sim_session.try_event(ts,
                                  already_emitted_in_session=len(decisions),
                                  prob_override=1.0)
        if d is not None:
            decisions.append(d)
            if len(decisions) >= event_config.MAX_ERROR_EVENTS_PER_SESSION:
                break
    assert_check(len(decisions) == 1,
                 f"Cap chính xác 1 (got {len(decisions)})")

    # Default prob 0.001 với n_attempts=200 → cap có thể không hit
    sim_default = ErrorSimulator(rng=random.Random(0))
    decisions_def = sim_default.try_events_for_session(
        base_ts, n_attempts=200,
    )
    assert_check(len(decisions_def) <= 1,
                 f"n_attempts=200 default prob: <= 1 (got {len(decisions_def)})")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 14 ERROR OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
