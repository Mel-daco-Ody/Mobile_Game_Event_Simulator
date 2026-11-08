"""Smoke test cho BƯỚC 5 – Session Model.

Kiểm tra:
    1. Session sinh đúng UUID v4, không trùng giữa 2 session.
    2. session_num = player.session_count + 1 (tiếp theo).
    3. Duration range đúng theo engagement segment.
    4. close() tính đúng duration_seconds.
    5. close() raise ValueError nếu end_ts < start_ts.
    6. Snapshot: engagement_segment & platform không đổi khi session tạo.
    7. to_dict / from_dict round-trip giữ nguyên dữ liệu.
    8. generate_session với rng cố định -> tái lập được.

Chạy:
    cd prj && python models/smoke_test.py
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

# UTF-8 cho stdout
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from generators import generate_players  # noqa: E402
from models import Player, Session  # noqa: E402
from models.session import (  # noqa: E402
    generate_session,
    SESSION_DURATION_SECONDS,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 5: SESSION MODEL")
    print("=" * 60)

    players = generate_players(count=10, seed=1)
    player: Player = players[0]
    base_ts = int(datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())

    # ------------------------------------------------------------------
    # 1. UUID v4 format và không trùng nhau
    # ------------------------------------------------------------------
    print("\n[1] Session sinh đúng UUID v4, không trùng")
    s1 = generate_session(player, session_start_ts=base_ts, rng=__import__("random").Random(1))
    s2 = generate_session(player, session_start_ts=base_ts, rng=__import__("random").Random(2))

    # UUID v4 regex pattern: 8-4-4-4-12 hex chars
    def is_uuid4(s: str) -> bool:
        try:
            u = uuid.UUID(s)
            return u.version == 4
        except (ValueError, TypeError):
            return False

    valid = is_uuid4(s1.session_id)
    assert_check(valid, f"session_id là UUID v4: {s1.session_id}")
    assert_check(s1.session_id != s2.session_id, "2 session khác nhau không trùng UUID")

    # ------------------------------------------------------------------
    # 2. session_num = player.session_count + 1
    # ------------------------------------------------------------------
    print("\n[2] session_num = player.session_count + 1")
    player.session_count = 0
    for i in range(1, 6):
        s = generate_session(player, session_start_ts=base_ts + i * 100)
        assert_check(s.session_num == i, f"session #{i} -> session_num={s.session_num}")
        player.session_count += 1  # simulate game incrementing after session_end

    # ------------------------------------------------------------------
    # 3. Duration range đúng theo engagement
    # ------------------------------------------------------------------
    print("\n[3] Duration range đúng theo engagement segment")
    test_players = [
        (p, seg) for p in generate_players(count=100, seed=7)
        for seg in [p.engagement_segment]
    ]

    for seg, (min_s, max_s) in SESSION_DURATION_SECONDS.items():
        seg_players = [p for p in generate_players(count=200, seed=ord(seg[0]) * 17)
                       if p.engagement_segment == seg]
        for p in seg_players[:20]:
            rng = __import__("random").Random(hash(seg + p.user_id))
            s = generate_session(p, session_start_ts=base_ts, rng=rng)
            s.close(end_ts=s.session_start_ts + rng.randint(min_s, max_s))
            dur = s.duration_seconds
            assert min_s <= dur <= max_s, \
                f"{seg}: dur={dur} nằm ngoài [{min_s}, {max_s}]"
        print(f"  [OK] {seg:10s} [{min_s:5d}, {max_s:5d}] giây")

    # ------------------------------------------------------------------
    # 4. close() tính đúng duration_seconds
    # ------------------------------------------------------------------
    print("\n[4] close() tính đúng duration_seconds")
    s = generate_session(player, session_start_ts=base_ts)
    assert_check(s.duration_seconds == 0, "duration=0 khi chưa close")
    assert_check(s.is_open, "is_open=True khi chưa close")
    s.close(end_ts=base_ts + 3600)
    assert_check(s.duration_seconds == 3600, f"duration=3600 (1h), got={s.duration_seconds}")
    assert_check(not s.is_open, "is_open=False sau close")
    assert_check(s.session_end_ts == base_ts + 3600, "session_end_ts đúng")
    assert_check(s.end_datetime == datetime.fromtimestamp(base_ts + 3600, tz=timezone.utc),
                 "end_datetime đúng")

    # ------------------------------------------------------------------
    # 5. close() raise ValueError nếu end_ts < start_ts
    # ------------------------------------------------------------------
    print("\n[5] close() raise ValueError nếu end_ts < start_ts")
    bad = generate_session(player, session_start_ts=base_ts + 1000)
    try:
        bad.close(end_ts=base_ts)  # end_ts < start_ts -> phải raise
        raise AssertionError("Không raise ValueError khi end_ts < start_ts")
    except ValueError as e:
        assert_check(True, f"ValueError raised: {e}")

    # ------------------------------------------------------------------
    # 6. Snapshot engagement_segment & platform không đổi khi sinh
    # ------------------------------------------------------------------
    print("\n[6] Snapshot engagement_segment & platform không đổi")
    p = players[2]
    original_seg = p.engagement_segment
    original_plat = p.platform
    s = generate_session(p, session_start_ts=base_ts)
    assert_check(s.engagement_segment == original_seg,
                 f"engagement_segment snapshot: {s.engagement_segment}")
    assert_check(s.platform == original_plat,
                 f"platform snapshot: {s.platform}")

    # ------------------------------------------------------------------
    # 7. to_dict / from_dict round-trip giữ nguyên dữ liệu
    # ------------------------------------------------------------------
    print("\n[7] to_dict / from_dict round-trip")
    s = generate_session(player, session_start_ts=base_ts)
    s.close(end_ts=base_ts + 900)
    d = s.to_dict()
    restored = Session.from_dict(d)
    for key in ("session_id", "user_id", "session_num", "session_start_ts",
                "session_end_ts", "duration_seconds", "connection_type",
                "engagement_segment", "platform"):
        assert_check(
            getattr(s, key) == getattr(restored, key),
            f"field {key}: {getattr(s, key)} == {getattr(restored, key)}"
        )

    # ------------------------------------------------------------------
    # 8. generate_session với rng cố định -> tái lập được các field còn lại
    #
    # Lưu ý: UUID v4 dùng OS entropy (random bảo mật), KHÔNG phụ thuộc
    # random.Random của Python. Do đó session_id sẽ khác nhau giữa các lần.
    # Các field khác (connection_type, session_num, user_id, engagement, platform)
    # phải tái lập được với cùng seed.
    # ------------------------------------------------------------------
    print("\n[8] generate_session với seed cố định -> tái lập được (trừ UUID)")
    rng1 = __import__("random").Random(99)
    rng2 = __import__("random").Random(99)
    p = players[3]
    s_a = generate_session(p, session_start_ts=base_ts, rng=rng1)
    s_b = generate_session(p, session_start_ts=base_ts, rng=rng2)
    assert_check(
        s_a.connection_type == s_b.connection_type,
        "Cùng seed -> cùng connection_type"
    )
    assert_check(
        s_a.user_id == s_b.user_id and s_a.session_num == s_b.session_num,
        "Cùng seed -> cùng user_id & session_num"
    )
    assert_check(
        s_a.engagement_segment == s_b.engagement_segment,
        "Cùng seed -> cùng engagement_segment"
    )
    assert_check(
        s_a.platform == s_b.platform,
        "Cùng seed -> cùng platform"
    )

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 5 SESSION MODEL OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
