"""Smoke test cho BƯỚC 6 – GameAnalytics Annotations.

Kiểm tra:
    1. create_default_annotations trả đúng 11 fields bắt buộc.
    2. sdk_version bắt buộc là 'rest api v2', không đổi được.
    3. client_ts là Unix timestamp (int), không phải ISO string.
    4. user_id, session_id, session_num đúng từ player & session.
    5. os_version, manufacturer, device, platform đúng từ player.
    6. server_ts_offset thay đổi được (set_server_ts_offset).
    7. client_ts có cộng offset khi đặt offset != 0.
    8. build = '1.0.0' (từ game_config).
    9. Tái lập được: cùng player/session/client_ts -> cùng annotations.

Chạy:
    cd prj && python gameanalytics/smoke_test.py
"""

from __future__ import annotations

import os
import sys
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

from config import game_config  # noqa: E402
from generators import generate_players  # noqa: E402
from models import Player, Session  # noqa: E402
from models.session import generate_session  # noqa: E402
from gameanalytics.annotations import (  # noqa: E402
    create_default_annotations,
    set_server_ts_offset,
    get_server_ts_offset,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 6: GAMEANALYTICS ANNOTATIONS")
    print("=" * 60)

    # Reset offset về default trước khi test
    set_server_ts_offset(0)

    players = generate_players(count=3, seed=5)
    player: Player = players[0]
    base_ts = int(datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    session = generate_session(player, session_start_ts=base_ts)
    session.close(end_ts=base_ts + 600)

    # ------------------------------------------------------------------
    # 1. Đủ 11 fields bắt buộc
    # ------------------------------------------------------------------
    print("\n[1] create_default_annotations trả đúng 11 fields bắt buộc")
    ann = create_default_annotations(player, session, client_ts=base_ts)
    required_keys = {
        "v", "user_id", "client_ts", "sdk_version",
        "os_version", "manufacturer", "device", "platform",
        "session_id", "session_num", "build",
    }
    assert_check(set(ann.keys()) == required_keys,
                 f"Tất cả 11 field có mặt: {sorted(ann.keys())}")

    # ------------------------------------------------------------------
    # 2. sdk_version bắt buộc là 'rest api v2'
    # ------------------------------------------------------------------
    print("\n[2] sdk_version = 'rest api v2' (không đổi được)")
    assert_check(
        ann["sdk_version"] == "rest api v2",
        f"sdk_version = '{ann['sdk_version']}'"
    )
    assert_check(
        ann["sdk_version"] == game_config.SDK_VERSION,
        "sdk_version khớp với game_config.SDK_VERSION"
    )
    assert_check(
        ann["sdk_version"] != "Python 1.0",
        "sdk_version không phải 'Python 1.0' (không tự đặt)"
    )
    assert_check(
        ann["sdk_version"] != "Simulator 1.0",
        "sdk_version không phải 'Simulator 1.0' (không tự đặt)"
    )

    # ------------------------------------------------------------------
    # 3. client_ts là Unix timestamp (int), không phải ISO string
    # ------------------------------------------------------------------
    print("\n[3] client_ts là Unix timestamp (int), không phải ISO string")
    assert_check(isinstance(ann["client_ts"], int),
                 f"client_ts là int, không phải {type(ann['client_ts']).__name__}")
    assert_check(ann["client_ts"] == base_ts,
                 f"client_ts = {ann['client_ts']} (khớp với base_ts={base_ts})")
    assert_check(
        str(ann["client_ts"]).isdigit(),
        "client_ts chỉ chứa chữ số (timestamp)"
    )

    # ------------------------------------------------------------------
    # 4. user_id, session_id, session_num đúng
    # ------------------------------------------------------------------
    print("\n[4] user_id, session_id, session_num đúng từ player & session")
    assert_check(ann["user_id"] == player.user_id,
                 f"user_id = '{ann['user_id']}'")
    assert_check(ann["session_id"] == session.session_id,
                 f"session_id = '{ann['session_id']}'")
    assert_check(ann["session_num"] == session.session_num,
                 f"session_num = {ann['session_num']}")
    assert_check(ann["session_num"] >= 1,
                 "session_num >= 1 (bắt đầu từ 1)")

    # ------------------------------------------------------------------
    # 5. os_version, manufacturer, device, platform đúng từ player
    # ------------------------------------------------------------------
    print("\n[5] os_version, manufacturer, device, platform đúng từ player")
    assert_check(ann["os_version"] == player.os_version,
                 f"os_version = '{ann['os_version']}'")
    assert_check(ann["manufacturer"] == player.manufacturer,
                 f"manufacturer = '{ann['manufacturer']}'")
    assert_check(ann["device"] == player.device,
                 f"device = '{ann['device']}'")
    assert_check(ann["platform"] == player.platform,
                 f"platform = '{ann['platform']}'")
    assert_check(ann["platform"] in ("android", "ios"),
                 "platform là 'android' hoặc 'ios'")

    # ------------------------------------------------------------------
    # 6. server_ts_offset có thể thay đổi
    # ------------------------------------------------------------------
    print("\n[6] server_ts_offset thay đổi được (set_server_ts_offset)")
    assert_check(get_server_ts_offset() == 0,
                 "Offset mặc định = 0")
    set_server_ts_offset(30)
    assert_check(get_server_ts_offset() == 30,
                 "Offset sau khi set = 30")
    set_server_ts_offset(-5)
    assert_check(get_server_ts_offset() == -5,
                 "Offset sau khi set = -5 (âm hợp lệ)")
    set_server_ts_offset(0)  # reset

    # ------------------------------------------------------------------
    # 7. client_ts cộng offset khi offset != 0
    # ------------------------------------------------------------------
    print("\n[7] client_ts có cộng offset khi set_server_ts_offset != 0")
    set_server_ts_offset(10)
    ann_offset = create_default_annotations(player, session, client_ts=base_ts)
    assert_check(
        ann_offset["client_ts"] == base_ts + 10,
        f"client_ts = {ann_offset['client_ts']} == base_ts + 10"
    )
    set_server_ts_offset(0)  # reset

    # ------------------------------------------------------------------
    # 8. build = '1.0.0' (từ game_config)
    # ------------------------------------------------------------------
    print("\n[8] build = '1.0.0' (từ game_config)")
    assert_check(ann["build"] == game_config.GAME_BUILD,
                 f"build = '{ann['build']}'")
    assert_check(ann["build"] == "1.0.0",
                 "build = '1.0.0'")

    # ------------------------------------------------------------------
    # 9. Tái lập được: cùng input -> cùng output
    # ------------------------------------------------------------------
    print("\n[9] Cùng player/session/client_ts -> cùng annotations (tái lập)")
    ann_a = create_default_annotations(player, session, client_ts=base_ts)
    ann_b = create_default_annotations(player, session, client_ts=base_ts)
    for key in ann_a:
        assert_check(
            ann_a[key] == ann_b[key],
            f"Field '{key}' tái lập: {ann_a[key]} == {ann_b[key]}"
        )

    # ------------------------------------------------------------------
    # 10. Field v = 2 (event version theo GameAnalytics schema)
    # ------------------------------------------------------------------
    print("\n[10] v (event_version) = 2 theo GameAnalytics schema")
    assert_check(ann["v"] == 2, f"v = {ann['v']}")
    assert_check(ann["v"] == game_config.EVENT_VERSION,
                 f"v == game_config.EVENT_VERSION ({game_config.EVENT_VERSION})")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 6 ANNOTATIONS OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
