"""Smoke test cho BƯỚC 16 – Event Queue (Retry Queue).

Kiểm tra EventQueue:

A. Init & Migration (idempotent):
    1. init() thêm các cột retry_count/last_attempt_at/next_retry_at/last_error
       (idempotent với bảng đã có).
    2. Init trên bảng TRỐNG (chưa có queue) – tạo bảng + cột.
    3. Init trên bảng đã migrate – KHÔNG lỗi.

B. Producer API:
    4. enqueue(event) trả id > 0.
    5. enqueue_batch([10 events]) trả 10 id.
    6. Payload được JSON serialize đúng (round-trip).

C. Consumer API:
    7. dequeue_batch(limit=10) trả event pending (FIFO theo id).
    8. count_by_status('pending') == số event vừa enqueue.
    9. dequeue_batch với status='sent' trả [] (chưa có).
    10. Status invalid raise ValueError.

D. Retry Flow (mark_failed + exponential backoff):
    11. mark_sent([id]) → status='sent'.
    12. mark_failed(id, "timeout") → status='failed', retry_count=1.
    13. next_retry_at = now + 30s (MIN_RETRY_DELAY).
    14. mark_failed lần 2 → retry_count=2, delay = 60s.
    15. mark_failed lần 3 → retry_count=3, delay = 120s.
    16. exponential backoff cap tại MAX_RETRY_DELAY_SECONDS.

E. Reschedule:
    17. reschedule_failed() khi now < next_retry_at → KHÔNG move.
    18. reschedule_failed() sau khi fake now >= next_retry_at → move về pending.
    19. reschedule KHÔNG move event đã quá max_retry (dead letter).

F. Stats:
    20. stats() đếm đúng pending/failed/sent/dead.
    21. get_dead_letters() trả về các event quá max_retry.

G. Maintenance:
    22. purge_sent(older_than_days=0) xoá sent ngay lập tức.
    23. clear_all() xoá sạch.

H. Persistence:
    24. Sau khi reopen EventQueue trên cùng DB → state vẫn còn.
    25. KHÔNG xoá event khi mark_failed – data integrity.

Chạy:
    cd prj && python storage/smoke_test_queue.py
"""

from __future__ import annotations

import json
import os
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

# Test dùng DB riêng
_TEST_DB = os.path.join(_PROJECT_ROOT, "simulator_queue_smoketest.db")
os.environ["SIMULATOR_DB"] = _TEST_DB

from storage import (  # noqa: E402
    ALL_STATUSES,
    BACKOFF_MULTIPLIER,
    EventQueue,
    MAX_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    MIN_RETRY_DELAY_SECONDS,
    QueuedEvent,
    QueueStats,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
    SENT_RETENTION_DAYS,
    SQLiteStorage,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def fresh_db() -> EventQueue:
    """Tạo DB mới, return EventQueue đã init."""
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    for s in ("-wal", "-shm"):
        p = _TEST_DB + s
        if os.path.exists(p):
            os.remove(p)
    with SQLiteStorage() as db:
        db.init_schema()
    # Tạo EventQueue trỏ vào DB mới
    storage = SQLiteStorage()
    eq = EventQueue(storage)
    eq.init()
    return eq


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 16: EVENT QUEUE (RETRY)")
    print("=" * 60)

    # ----------------------------------------------------------------------
    # A. Init & Migration
    # ----------------------------------------------------------------------
    print("\n[--- A. INIT & MIGRATION ---]")

    # A1. Init trên bảng TRỐNG
    print("\n[A1] init() thêm 4 cột retry (idempotent)")
    eq = fresh_db()
    # Kiểm tra các cột đã có
    with SQLiteStorage() as db:
        cols = db.conn.execute("PRAGMA table_info(queue)").fetchall()
        col_names = {c["name"] for c in cols}
    for c in ("retry_count", "last_attempt_at", "next_retry_at", "last_error"):
        assert_check(c in col_names,
                     f"queue.{c} có trong schema")

    # A2. Init idempotent (chạy lại KHÔNG lỗi)
    print("\n[A2] init() idempotent")
    eq.init()
    eq.init()
    eq.init()
    assert_check(True, "init() chạy 3 lần không lỗi")

    # A3. Indexes
    print("\n[A3] Indexes retry đã tạo")
    with SQLiteStorage() as db:
        idx = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='queue' ORDER BY name"
        ).fetchall()
        idx_names = {r["name"] for r in idx}
    assert_check("idx_queue_status_retry" in idx_names,
                 "idx_queue_status_retry có")
    assert_check("idx_queue_pending" in idx_names,
                 "idx_queue_pending có")

    # ----------------------------------------------------------------------
    # B. Producer API
    # ----------------------------------------------------------------------
    print("\n[--- B. PRODUCER API ---]")

    print("\n[B1] enqueue(event) trả id > 0")
    eq.clear_all()
    sample_event = {
        "category": "user",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600,
        "sdk_version": "rest api v2",
        "os_version": "android 15",
        "manufacturer": "google",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
    }
    eid = eq.enqueue(sample_event)
    assert_check(eid > 0, f"enqueue trả id={eid} > 0")

    print("\n[B2] enqueue_batch([10 events]) trả 10 id")
    eq.clear_all()
    batch_10 = [dict(sample_event, client_ts=1759305600 + i * 10) for i in range(10)]
    ids = eq.enqueue_batch(batch_10)
    assert_check(len(ids) == 10,
                 f"enqueue_batch trả 10 id (got {len(ids)})")
    # id tăng dần
    assert_check(ids == sorted(ids), "ids tăng dần")
    # count_by_status
    assert_check(eq.count_by_status(STATUS_PENDING) == 10,
                 f"pending count = {eq.count_by_status(STATUS_PENDING)}")

    print("\n[B3] Payload JSON round-trip")
    eq.clear_all()
    eid = eq.enqueue(sample_event)
    ev = eq.get(eid)
    assert_check(ev.payload["user_id"] == "player_000001",
                 f"payload.user_id khớp")
    assert_check(ev.payload["session_num"] == 1,
                 f"payload.session_num khớp")
    assert_check(ev.payload == sample_event,
                 "payload round-trip exact")

    # ----------------------------------------------------------------------
    # C. Consumer API
    # ----------------------------------------------------------------------
    print("\n[--- C. CONSUMER API ---]")

    print("\n[C1] dequeue_batch FIFO theo id")
    eq.clear_all()
    eq.enqueue(dict(sample_event, session_num=1))
    eq.enqueue(dict(sample_event, session_num=2))
    eq.enqueue(dict(sample_event, session_num=3))
    batch = eq.dequeue_batch(limit=10)
    assert_check(len(batch) == 3, f"dequeue 3 events (got {len(batch)})")
    session_nums = [ev.payload["session_num"] for ev in batch]
    assert_check(session_nums == [1, 2, 3],
                 f"FIFO: session_num = {session_nums}")
    for ev in batch:
        assert_check(ev.is_pending, f"event id={ev.id} status=pending")

    print("\n[C2] dequeue với status='sent' trả []")
    eq.clear_all()
    eq.enqueue(sample_event)
    eq.mark_sent(eq.enqueue(sample_event))   # pending -> sent
    eq.enqueue(sample_event)
    pending = eq.dequeue_batch(status=STATUS_PENDING)
    sent = eq.dequeue_batch(status=STATUS_SENT)
    assert_check(len(pending) == 2, f"2 pending (got {len(pending)})")
    assert_check(len(sent) == 1, f"1 sent (got {len(sent)})")

    print("\n[C3] Status invalid raise ValueError")
    try:
        eq.dequeue_batch(status="garbage")
        raise AssertionError("Không raise với status='garbage'")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # D. Retry Flow
    # ----------------------------------------------------------------------
    print("\n[--- D. RETRY FLOW ---]")

    print("\n[D1] mark_sent([id]) → status='sent'")
    eq.clear_all()
    eid = eq.enqueue(sample_event)
    rc = eq.mark_sent(eid)
    assert_check(rc == 1, f"mark_sent 1 row")
    ev = eq.get(eid)
    assert_check(ev.is_sent, f"event {eid} is_sent")
    assert_check(ev.sent_at is not None, f"sent_at set")

    print("\n[D2] mark_failed → retry_count=1, delay=30s")
    eq.clear_all()
    eid = eq.enqueue(sample_event)
    now_before = time.time()
    new_retry = eq.mark_failed(eid, "Network timeout")
    after = time.time()
    assert_check(new_retry == 1, f"new_retry=1 (got {new_retry})")
    ev = eq.get(eid)
    assert_check(ev.is_failed, f"event {eid} is_failed")
    assert_check(ev.retry_count == 1, f"retry_count=1")
    # next_retry_at ~ now + 30s (delay MIN_RETRY_DELAY_SECONDS)
    assert_check(ev.next_retry_at is not None, "next_retry_at set")
    if ev.next_retry_at:
        delta = (ev.next_retry_at.timestamp() - now_before)
        # delay = 30, +/- 5s slack
        assert_check(
            MIN_RETRY_DELAY_SECONDS - 5 <= delta <= MIN_RETRY_DELAY_SECONDS + 5,
            f"delta = {delta:.1f}s ∈ [25, 35]"
        )

    print("\n[D3] mark_failed lần 2 → retry_count=2, delay=60s")
    eq.mark_failed(eid, "Timeout 2")
    ev = eq.get(eid)
    assert_check(ev.retry_count == 2, f"retry_count=2")
    if ev.next_retry_at:
        # delay now (since last_attempt_at)
        delay_2 = (ev.next_retry_at - ev.last_attempt_at).total_seconds()
        expected_2 = MIN_RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER ** 1)
        assert_check(abs(delay_2 - expected_2) < 1,
                     f"delay_2 = {delay_2:.1f}s ~ {expected_2}s")

    print("\n[D4] mark_failed lần 3 → retry_count=3, delay=120s")
    eq.mark_failed(eid, "Timeout 3")
    ev = eq.get(eid)
    assert_check(ev.retry_count == 3, f"retry_count=3")
    if ev.next_retry_at:
        delay_3 = (ev.next_retry_at - ev.last_attempt_at).total_seconds()
        expected_3 = MIN_RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER ** 2)
        assert_check(abs(delay_3 - expected_3) < 1,
                     f"delay_3 = {delay_3:.1f}s ~ {expected_3}s")

    print("\n[D5] Exponential backoff cap tại MAX_RETRY_DELAY_SECONDS")
    eq.clear_all()
    eid_cap = eq.enqueue(sample_event)
    # Fail đủ MAX_RETRIES+2 lần → cap tại MAX_RETRY_DELAY_SECONDS
    for _ in range(MAX_RETRIES + 2):
        eq.mark_failed(eid_cap, "recurrent fail")
    ev = eq.get(eid_cap)
    if ev.next_retry_at and ev.last_attempt_at:
        delay = (ev.next_retry_at - ev.last_attempt_at).total_seconds()
        assert_check(delay <= MAX_RETRY_DELAY_SECONDS,
                     f"delay={delay:.1f}s <= {MAX_RETRY_DELAY_SECONDS}")

    # ----------------------------------------------------------------------
    # E. Reschedule
    # ----------------------------------------------------------------------
    print("\n[--- E. RESCHEDULE ---]")

    print("\n[E1] reschedule_failed() KHÔNG move khi chưa đến hạn")
    eq.clear_all()
    eid = eq.enqueue(sample_event)
    eq.mark_failed(eid, "timeout")
    moved = eq.reschedule_failed()
    assert_check(moved == 0, f"0 rescheduled (got {moved})")
    ev = eq.get(eid)
    assert_check(ev.is_failed, "vẫn failed")

    print("\n[E2] reschedule MOVE khi đến hạn (manual update next_retry_at)")
    eq.clear_all()
    eid = eq.enqueue(sample_event)
    eq.mark_failed(eid, "timeout")
    # Hack: set next_retry_at = quá khứ để test reschedule
    with SQLiteStorage() as db:
        db.conn.execute(
            "UPDATE queue SET next_retry_at = '1970-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (eid,),
        )
    moved = eq.reschedule_failed()
    assert_check(moved == 1, f"1 rescheduled (got {moved})")
    ev = eq.get(eid)
    assert_check(ev.is_pending, f"event {eid} → pending")

    print("\n[E3] reschedule KHÔNG move event quá MAX_RETRIES (dead letter)")
    eq.clear_all()
    eid_dead = eq.enqueue(sample_event)
    # Fail đủ MAX_RETRIES lần → retry_count = MAX_RETRIES
    for i in range(MAX_RETRIES):
        eq.mark_failed(eid_dead, f"fail {i+1}")
    # Đảm bảo next_retry_at đã quá khứ
    with SQLiteStorage() as db:
        db.conn.execute(
            "UPDATE queue SET next_retry_at = '1970-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (eid_dead,),
        )
    moved = eq.reschedule_failed()
    assert_check(moved == 0, f"dead letter KHÔNG reschedule (got {moved})")
    ev = eq.get(eid_dead)
    assert_check(ev.is_failed, "vẫn failed (dead)")

    # ----------------------------------------------------------------------
    # F. Stats
    # ----------------------------------------------------------------------
    print("\n[--- F. STATS ---]")

    print("\n[F1] stats() đếm đúng pending/failed/sent/dead")
    eq.clear_all()
    eq.enqueue(sample_event)  # pending
    eq.enqueue(sample_event)  # pending
    eq.mark_sent(eq.enqueue(sample_event))  # sent
    eq.mark_sent(eq.enqueue(sample_event))  # sent
    eq.mark_sent(eq.enqueue(sample_event))  # sent
    dead_id = eq.enqueue(sample_event)
    for i in range(MAX_RETRIES):
        eq.mark_failed(dead_id, f"fail {i+1}")  # dead (retry_count=MAX_RETRIES)
    live_failed = eq.enqueue(sample_event)
    eq.mark_failed(live_failed, "timeout 1")  # failed retry_count=1 (live)
    s = eq.stats()
    assert_check(s.pending == 2, f"pending=2 (got {s.pending})")
    assert_check(s.sent == 3, f"sent=3 (got {s.sent})")
    assert_check(s.dead == 1, f"dead=1 (got {s.dead})")
    # failed = live_failed (đã trừ dead)
    assert_check(s.failed == 1, f"failed=1 (got {s.failed})")
    # total = pending + failed + sent (dead đã tính trong failed)
    assert_check(s.total == 6, f"total=6 (got {s.total})")
    print(f"  [INFO] {s}")

    print("\n[F2] get_dead_letters() trả dead events")
    deads = eq.get_dead_letters()
    assert_check(len(deads) == 1, f"1 dead (got {len(deads)})")
    assert_check(deads[0].id == dead_id, f"dead id={dead_id}")

    # ----------------------------------------------------------------------
    # G. Maintenance
    # ----------------------------------------------------------------------
    print("\n[--- G. MAINTENANCE ---]")

    print("\n[G1] purge_sent(older_than_days=0) xoá sent ngay")
    eq.clear_all()
    eq.mark_sent(eq.enqueue(sample_event))
    eq.mark_sent(eq.enqueue(sample_event))
    eq.enqueue(sample_event)  # pending - KHÔNG purge
    purged = eq.purge_sent(older_than_days=0)
    assert_check(purged == 2, f"purge 2 sent (got {purged})")
    assert_check(eq.count_by_status(STATUS_PENDING) == 1,
                 "1 pending còn lại")
    assert_check(eq.count_by_status(STATUS_SENT) == 0, "0 sent còn lại")

    print("\n[G2] clear_all() xoá sạch")
    eq.clear_all()
    eq.enqueue(sample_event)
    eq.enqueue(sample_event)
    cleared = eq.clear_all()
    assert_check(cleared == 2, f"clear 2 rows (got {cleared})")
    assert_check(eq.count_by_status(STATUS_PENDING) == 0, "0 pending")

    # ----------------------------------------------------------------------
    # H. Persistence
    # ----------------------------------------------------------------------
    print("\n[--- H. PERSISTENCE ---]")

    print("\n[H1] State persist qua reopen")
    eq.clear_all()
    eid_p = eq.enqueue(sample_event)
    eq.mark_failed(eid_p, "test")
    # Reopen EventQueue
    eq2 = EventQueue(SQLiteStorage())
    eq2.init()
    ev2 = eq2.get(eid_p)
    assert_check(ev2 is not None, "event vẫn còn sau reopen")
    assert_check(ev2.is_failed, "vẫn failed sau reopen")
    assert_check(ev2.retry_count == 1, f"retry_count=1 sau reopen")
    assert_check(ev2.last_error == "test",
                 f"last_error='{ev2.last_error}'")

    print("\n[H2] KHÔNG xoá event khi mark_failed – data integrity")
    eq.clear_all()
    pre_count = eq.count_by_status(STATUS_PENDING) + eq.count_by_status(STATUS_FAILED)
    eid_i = eq.enqueue(sample_event)
    eq.mark_failed(eid_i, "x")
    eq.mark_failed(eid_i, "x")
    eq.mark_failed(eid_i, "x")
    post_count = eq.count_by_status(STATUS_PENDING) + eq.count_by_status(STATUS_FAILED)
    assert_check(post_count == pre_count + 1,
                 f"event vẫn còn (count: {pre_count} -> {post_count})")

    # ----------------------------------------------------------------------
    # Constants
    # ----------------------------------------------------------------------
    print("\n[--- CONSTANTS ---]")
    assert_check(STATUS_PENDING == "pending", "STATUS_PENDING='pending'")
    assert_check(STATUS_SENT == "sent", "STATUS_SENT='sent'")
    assert_check(STATUS_FAILED == "failed", "STATUS_FAILED='failed'")
    assert_check(len(ALL_STATUSES) == 3,
                 f"ALL_STATUSES = 3 (got {len(ALL_STATUSES)})")
    assert_check(BACKOFF_MULTIPLIER == 2, "BACKOFF_MULTIPLIER=2")
    assert_check(MIN_RETRY_DELAY_SECONDS == 30,
                 f"MIN_RETRY_DELAY_SECONDS = {MIN_RETRY_DELAY_SECONDS}")
    assert_check(MAX_RETRY_DELAY_SECONDS == 3600,
                 f"MAX_RETRY_DELAY_SECONDS = {MAX_RETRY_DELAY_SECONDS}")
    assert_check(SENT_RETENTION_DAYS == 7,
                 f"SENT_RETENTION_DAYS = {SENT_RETENTION_DAYS}")
    assert_check(MAX_RETRIES == 10, f"MAX_RETRIES = {MAX_RETRIES}")

    # Cleanup
    print("\n[Cleanup]")
    eq.clear_all()
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    for s in ("-wal", "-shm"):
        p = _TEST_DB + s
        if os.path.exists(p):
            os.remove(p)

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 16 EVENT QUEUE OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
