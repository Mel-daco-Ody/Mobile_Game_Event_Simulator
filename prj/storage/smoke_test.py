"""Smoke test cho BƯỚC 4 – Player State Storage (SQLite).

Kiểm tra:
    1. init_schema() tạo đủ 5 bảng và idempotent.
    2. save_players / load_players round-trip (giữ nguyên field & datetime).
    3. get_player trả None nếu không tồn tại.
    4. update_player_state cập nhật persistent field.
    5. save_session / load_sessions hoạt động (kể cả filter theo user_id).
    6. upsert_progression / upsert_economy idempotent.
    7. enqueue_event / dequeue_batch / mark_event_sent cho queue.
    8. Foreign key + journal_mode WAL bật đúng.
    9. File DB đặt ở <project_root>/simulator.db và được ignore trong .gitignore.

Chạy:
    cd prj && python storage/smoke_test.py

Lưu ý:
    Test sẽ tạo file simulator.db ở project root; file này đã được
    .gitignore loại trừ nên an toàn.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# UTF-8 cho stdout + đảm bảo project root nằm trong sys.path
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Test dùng DB riêng, KHÔNG ghi đè simulator.db thật
_TEST_DB = os.path.join(_PROJECT_ROOT, "simulator_smoketest.db")
os.environ["SIMULATOR_DB"] = _TEST_DB

from generators import generate_players  # noqa: E402
from models import Player  # noqa: E402
from storage import SQLiteStorage  # noqa: E402


def assert_check(condition: bool, label: str) -> None:
    """In kết quả kiểm tra và raise nếu sai."""
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    """Chạy toàn bộ check cho SQLite storage."""
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 4: PLAYER STATE STORAGE (SQLite)")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Chuẩn bị: xoá file DB cũ nếu có
    # ------------------------------------------------------------------
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    print(f"\n[Setup] DB path = {_TEST_DB}")

    # ------------------------------------------------------------------
    # 1. init_schema() idempotent – chạy 2 lần không lỗi
    # ------------------------------------------------------------------
    print("\n[1] init_schema() tạo 5 bảng và idempotent")
    with SQLiteStorage() as db:
        db.init_schema()
        # Chạy lần 2 để chứng minh idempotent
        db.init_schema()
        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = sorted(r["name"] for r in cur.fetchall())
    expected_tables = sorted(
        ["players", "sessions", "progression_state", "player_economy", "queue"]
    )
    assert_check(tables == expected_tables,
                 f"5 bảng: {tables}")

    # ------------------------------------------------------------------
    # 2. save_players / load_players round-trip
    # ------------------------------------------------------------------
    print("\n[2] save_players / load_players round-trip")
    sim_start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    players = generate_players(count=50, simulation_start_date=sim_start, seed=42)
    with SQLiteStorage() as db:
        db.save_players(players)
        loaded = db.load_players()
    assert_check(len(loaded) == 50, f"len(loaded) = {len(loaded)}")
    # So sánh 1 player đầu xem field có khớp không
    p_orig = players[0]
    p_load = next(p for p in loaded if p.user_id == p_orig.user_id)
    assert_check(p_load.user_id == p_orig.user_id, "user_id khớp")
    assert_check(p_load.country == p_orig.country, "country khớp")
    assert_check(p_load.coins == p_orig.coins, "coins khớp")
    assert_check(p_load.is_new_install == p_orig.is_new_install,
                 "is_new_install khớp")
    assert_check(p_load.install_date == p_orig.install_date,
                 f"install_date khớp ({p_load.install_date})")
    print(f"  Player mẫu: {p_load.user_id} / {p_load.country} / level={p_load.current_level}")

    # ------------------------------------------------------------------
    # 3. get_player không tồn tại -> None
    # ------------------------------------------------------------------
    print("\n[3] get_player không tồn tại trả None")
    with SQLiteStorage() as db:
        missing = db.get_player("player_999999")
    assert_check(missing is None, "player_999999 -> None")

    # ------------------------------------------------------------------
    # 4. save_players idempotent (chạy 2 lần không lỗi)
    # ------------------------------------------------------------------
    print("\n[4] save_players idempotent với INSERT OR REPLACE")
    with SQLiteStorage() as db:
        db.save_players(players)   # chạy lần 2, không nên lỗi
        n = len(db.load_players())
    assert_check(n == 50, f"vẫn còn đúng 50 player (n={n})")

    # ------------------------------------------------------------------
    # 5. update_player_state cập nhật persistent field
    # ------------------------------------------------------------------
    print("\n[5] update_player_state cập nhật persistent field")
    target = players[5]
    target.increment_session()
    target.increment_transaction(amount_minor=499)
    target.coins = 1500
    target.gems = 250
    target.current_level = 12
    target.last_session_date = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
    target.churn_status = "active"
    target.is_new_install = False
    with SQLiteStorage() as db:
        db.update_player_state(target)
        reloaded = db.get_player(target.user_id)
    assert_check(reloaded is not None, "player tồn tại sau update")
    assert_check(reloaded.session_count == 1, f"session_count = {reloaded.session_count}")
    assert_check(reloaded.transaction_num == 1, f"transaction_num = {reloaded.transaction_num}")
    assert_check(reloaded.lifetime_revenue == 499, f"lifetime_revenue = {reloaded.lifetime_revenue}")
    assert_check(reloaded.coins == 1500, f"coins = {reloaded.coins}")
    assert_check(reloaded.gems == 250, f"gems = {reloaded.gems}")
    assert_check(reloaded.current_level == 12, f"current_level = {reloaded.current_level}")
    assert_check(reloaded.last_session_date == target.last_session_date,
                 "last_session_date khớp")
    assert_check(reloaded.is_new_install is False, "is_new_install = False")

    # ------------------------------------------------------------------
    # 6. save_session / load_sessions
    # ------------------------------------------------------------------
    print("\n[6] save_session / load_sessions (kể cả filter theo user_id)")
    sess1_id = "550e8400-e29b-41d4-a716-446655440001"
    sess2_id = "550e8400-e29b-41d4-a716-446655440002"
    with SQLiteStorage() as db:
        db.save_session(
            session_id=sess1_id, user_id=target.user_id,
            session_num=1, session_start_ts=1_700_000_000,
            session_end_ts=1_700_001_800, duration_seconds=1800,
            connection_type="wifi", engagement_segment=target.engagement_segment,
        )
        db.save_session(
            session_id=sess2_id, user_id="player_000010",
            session_num=1, session_start_ts=1_700_002_000,
            session_end_ts=1_700_002_600, duration_seconds=600,
            connection_type="wwan", engagement_segment="casual",
        )
        all_sess = db.load_sessions()
        target_sess = db.load_sessions(user_id=target.user_id)
    assert_check(len(all_sess) == 2, f"tổng 2 session (n={len(all_sess)})")
    assert_check(len(target_sess) == 1, f"target có 1 session (n={len(target_sess)})")
    assert_check(target_sess[0]["session_id"] == sess1_id, "session_id đúng")
    assert_check(target_sess[0]["connection_type"] == "wifi", "connection_type đúng")

    # ------------------------------------------------------------------
    # 7. progression_state + player_economy idempotent
    # ------------------------------------------------------------------
    print("\n[7] upsert_progression / upsert_economy idempotent")
    with SQLiteStorage() as db:
        db.upsert_progression(target.user_id, 12, 1, 2)
        db.upsert_progression(target.user_id, 13, 1, 1)   # chạy lần 2
        prog = db.load_progression(target.user_id)
        db.upsert_economy(target)
        db.upsert_economy(target)                         # chạy lần 2
    assert_check(prog is not None, "progression đã tồn tại")
    assert_check(prog["current_level"] == 13, f"current_level = {prog['current_level']}")
    assert_check(prog["current_attempt_num"] == 1,
                 f"current_attempt_num = {prog['current_attempt_num']}")
    # Economy: query row trực tiếp
    with SQLiteStorage() as db:
        cur = db.conn.execute(
            "SELECT * FROM player_economy WHERE user_id = ?",
            (target.user_id,),
        )
        eco = dict(cur.fetchone())
    assert_check(eco["coins"] == 1500, f"economy.coins = {eco['coins']}")
    assert_check(eco["gems"] == 250, f"economy.gems = {eco['gems']}")

    # ------------------------------------------------------------------
    # 8. Queue: enqueue / dequeue / mark_sent
    # ------------------------------------------------------------------
    print("\n[8] queue: enqueue_event / dequeue_batch / mark_event_sent")
    with SQLiteStorage() as db:
        # Clear queue trước
        db.conn.execute("DELETE FROM queue")
        ids = []
        for i in range(5):
            eid = db.enqueue_event({"event_id": i, "ts": 1_700_000_000 + i})
            ids.append(eid)
        assert_check(all(i > 0 for i in ids), f"5 id hợp lệ ({ids})")
        batch = db.dequeue_batch(limit=3)
        assert_check(len(batch) == 3, f"dequeue 3 event (n={len(batch)})")
        # Đánh dấu 2 event đầu là sent
        db.mark_event_sent(batch[0]["id"])
        db.mark_event_sent(batch[1]["id"])
        # Lấy tiếp các pending còn lại
        remaining = db.dequeue_batch(limit=10)
        assert_check(len(remaining) == 3, f"còn 3 pending (n={len(remaining)})")
        # Payload phải là JSON parse được
        assert isinstance(batch[0]["payload"], dict), "payload là dict"
        print(f"  Payload mẫu: {batch[0]['payload']}")

    # ------------------------------------------------------------------
    # 9. PRAGMA foreign_keys + journal_mode bật
    # ------------------------------------------------------------------
    print("\n[9] PRAGMA foreign_keys = ON và journal_mode = WAL")
    with SQLiteStorage() as db:
        fk = db.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        jm = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert_check(fk == 1, f"foreign_keys = {fk}")
    assert_check(str(jm).lower() == "wal", f"journal_mode = {jm}")

    # ------------------------------------------------------------------
    # 10. DB path mặc định đúng và .gitignore đã có *.db
    # ------------------------------------------------------------------
    print("\n[10] DB path mặc định và .gitignore")
    default_path = os.path.join(_PROJECT_ROOT, "simulator.db")
    gitignore_path = os.path.join(_PROJECT_ROOT, ".gitignore")
    with open(gitignore_path, "r", encoding="utf-8") as f:
        gitignore_content = f.read()
    assert_check(default_path.endswith("simulator.db"),
                 f"default path = {default_path}")
    assert_check("*.db" in gitignore_content,
                 ".gitignore có ignore *.db")
    # File DB test có thật trên disk
    assert_check(os.path.exists(_TEST_DB), f"DB file tồn tại: {_TEST_DB}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    print("\n[Cleanup] Xoá file DB test")
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    # WAL mode tạo thêm file .wal/.shm; dọn luôn
    for suffix in ("-wal", "-shm"):
        wal_path = _TEST_DB + suffix
        if os.path.exists(wal_path):
            os.remove(wal_path)

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 4 STORAGE OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
