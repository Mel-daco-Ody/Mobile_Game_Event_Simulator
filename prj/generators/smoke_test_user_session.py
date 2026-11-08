"""Smoke test cho BƯỚC 7 & 8 – User Event & Session End Event.

Kiểm tra User event (Bước 7):
    1. category = 'user'.
    2. Đầy đủ 11 common annotations từ create_default_annotations().
    3. session_id, session_num đúng từ session.
    4. client_ts = session.session_start_ts (mặc định).
    5. KHÔNG có field riêng ngoài category.

Kiểm tra Session End event (Bước 8):
    6. category = 'session_end'.
    7. length = session.duration_seconds.
    8. length = session_end.client_ts - session_start.client_ts.
    9. 0 <= length <= 172800.
    10. Raise ValueError nếu session chưa close().

Chạy:
    cd prj && python generators/smoke_test_user_session.py
"""

from __future__ import annotations

import json
import os
import sys

# UTF-8 cho stdout
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import game_config  # noqa: E402
from generators import (  # noqa: E402
    generate_players,
    generate_session_end_event,
    generate_user_event,
)
from models.session import generate_session  # noqa: E402


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 7 & 8: USER + SESSION_END EVENT")
    print("=" * 60)

    players = generate_players(count=3, seed=42)
    player = players[0]
    player.session_count = 5  # Simulating player đã chơi 5 session

    # ============================================================
    # BƯỚC 7 – USER EVENT
    # ============================================================
    print("\n[--- BƯỚC 7: USER EVENT ---]")

    # ------------------------------------------------------------------
    # 1. category = 'user'
    # ------------------------------------------------------------------
    print("\n[1] generate_user_event: category = 'user'")
    session = generate_session(player)
    assert_check(session.session_num == 6,
                 f"session_num = {session.session_num} (player.session_count+1)")

    event = generate_user_event(player, session)
    assert_check(event["category"] == "user",
                 f"category = '{event['category']}'")

    # ------------------------------------------------------------------
    # 2. Đầy đủ 11 common annotations
    # ------------------------------------------------------------------
    print("\n[2] Đầy đủ 11 common annotations")
    required_keys = {
        "category", "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
    }
    actual_keys = set(event.keys())
    assert_check(required_keys <= actual_keys,
                 f"Có đủ {len(required_keys)} field: {sorted(actual_keys)}")

    # ------------------------------------------------------------------
    # 3. session_id, session_num đúng
    # ------------------------------------------------------------------
    print("\n[3] session_id, session_num đúng từ session")
    assert_check(event["session_id"] == session.session_id,
                 f"session_id = {event['session_id']}")
    assert_check(event["session_num"] == session.session_num,
                 f"session_num = {event['session_num']}")
    assert_check(event["user_id"] == player.user_id,
                 f"user_id = {event['user_id']}")

    # ------------------------------------------------------------------
    # 4. client_ts = session.session_start_ts
    # ------------------------------------------------------------------
    print("\n[4] client_ts = session.session_start_ts (mặc định)")
    assert_check(event["client_ts"] == session.session_start_ts,
                 f"client_ts = {event['client_ts']} == session_start_ts")
    assert_check(isinstance(event["client_ts"], int),
                 "client_ts là int (Unix timestamp)")

    # ------------------------------------------------------------------
    # 5. KHÔNG có field riêng ngoài category
    # ------------------------------------------------------------------
    print("\n[5] User event KHÔNG có field riêng ngoài category")
    extra_fields = actual_keys - required_keys
    assert_check(len(extra_fields) == 0,
                 f"User event chỉ có common annotations (extra={extra_fields})")

    # ------------------------------------------------------------------
    # Sample payload
    # ------------------------------------------------------------------
    print("\n[Sample] User event payload:")
    sample = json.dumps(event, ensure_ascii=False, indent=2)
    print(f"  {sample[:400]}...")

    # ============================================================
    # BƯỚC 8 – SESSION END EVENT
    # ============================================================
    print("\n[--- BƯỚC 8: SESSION_END EVENT ---]")

    # ------------------------------------------------------------------
    # 6. category = 'session_end'
    # ------------------------------------------------------------------
    print("\n[6] generate_session_end_event: category = 'session_end'")
    duration_sec = 842  # match INSTRUCTION example
    session.close(end_ts=session.session_start_ts + duration_sec)
    assert_check(not session.is_open, "Session đã close()")

    end_event = generate_session_end_event(player, session)
    assert_check(end_event["category"] == "session_end",
                 f"category = '{end_event['category']}'")

    # ------------------------------------------------------------------
    # 7. length = session.duration_seconds
    # ------------------------------------------------------------------
    print("\n[7] length = session.duration_seconds")
    assert_check(end_event["length"] == duration_sec,
                 f"length = {end_event['length']} == {duration_sec}")
    assert_check(end_event["length"] == session.duration_seconds,
                 f"length = {end_event['length']} == session.duration_seconds")

    # ------------------------------------------------------------------
    # 8. length = session_end.client_ts - session_start.client_ts
    # ------------------------------------------------------------------
    print("\n[8] length = end.client_ts - start.client_ts")
    derived_length = end_event["client_ts"] - session.session_start_ts
    assert_check(end_event["length"] == derived_length,
                 f"length = {end_event['length']} = {end_event['client_ts']} - {session.session_start_ts}")

    # ------------------------------------------------------------------
    # 9. 0 <= length <= 172800
    # ------------------------------------------------------------------
    print("\n[9] 0 <= length <= 172800")
    max_len = game_config.SESSION_MAX_LENGTH_SECONDS
    assert_check(0 <= end_event["length"] <= max_len,
                 f"length={end_event['length']} trong [0, {max_len}]")

    # Test đủ các segment
    print("\n  Test length hợp lệ theo segment:")
    seg_ranges = {
        "casual":    (60, 600),
        "regular":   (600, 1800),
        "engaged":   (1200, 3600),
        "hardcore":  (1800, 7200),
    }
    for seg, (lo, hi) in seg_ranges.items():
        p_players = [p for p in generate_players(count=10, seed=hash(seg) & 0xFFFFFFFF)
                     if p.engagement_segment == seg][:3]
        for p in p_players:
            dur = lo  # min duration
            s_test = generate_session(p)
            s_test.close(end_ts=s_test.session_start_ts + dur)
            ev = generate_session_end_event(p, s_test)
            assert_check(lo <= ev["length"] <= hi,
                         f"{seg}: length={ev['length']} trong [{lo}, {hi}]")

    # ------------------------------------------------------------------
    # 10. Raise ValueError nếu session chưa close()
    # ------------------------------------------------------------------
    print("\n[10] ValueError nếu session chưa close()")
    p2 = generate_players(count=1, seed=99)[0]
    open_session = generate_session(p2)
    try:
        generate_session_end_event(p2, open_session)
        raise AssertionError("Không raise ValueError khi session chưa close")
    except ValueError as e:
        assert_check(True, f"ValueError raised: {e}")

    # ------------------------------------------------------------------
    # Sample payload
    # ------------------------------------------------------------------
    print("\n[Sample] session_end event payload:")
    sample_end = json.dumps(end_event, ensure_ascii=False, indent=2)
    print(f"  {sample_end[:400]}...")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 7 & 8 USER/SESSION_END OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
