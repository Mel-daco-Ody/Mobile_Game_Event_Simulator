"""Event Queue – persistent SQLite-backed retry queue.

Theo BƯỚC 16 INSTRUCTION.md:

Cache event trước khi gửi GameAnalytics. Chịu được mất mạng.

Quy tắc:

    - Event fail POST → KHÔNG discard, chuyển sang trạng thái ``failed``
      với retry_count++ và next_retry_at = now + backoff.
    - Khi network restored (tức là now >= next_retry_at) → reschedule
      các event failed về ``pending`` để retry.
    - Persistent qua process restart (SQLite on disk).
    - Pattern GameAnalytics khuyến nghị: client cache + submit định kỳ.

State machine:

    pending  --(POST OK)-->  sent
    pending  --(POST fail)--> failed (retry_count++, next_retry_at = now + backoff)
    failed   --(now >= next_retry_at)--> pending  (reschedule)
    sent     --(older than retention)--> purged (tuỳ chọn)

Retry strategy (exponential backoff):

    retry_count 1  → wait 30s   (MIN_RETRY_DELAY_SECONDS)
    retry_count 2  → wait 60s
    retry_count 3  → wait 120s
    retry_count 4  → wait 240s
    ...
    retry_count N  → wait min(MAX_RETRY_DELAY_SECONDS, 30 * 2^(N-1))

    Sau MAX_RETRIES lần, vẫn giữ event trong queue nhưng KHÔNG reschedule
    thêm (để operator can thiệp). KHÔNG xoá data – rule data quality.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from storage.sqlite import SQLiteStorage


# =========================================================================
# Constants
# =========================================================================

# Status enum (cũng được lưu vào DB schema)
STATUS_PENDING: str = "pending"
STATUS_SENT: str = "sent"
STATUS_FAILED: str = "failed"

ALL_STATUSES: frozenset[str] = frozenset({
    STATUS_PENDING, STATUS_SENT, STATUS_FAILED,
})

# Backoff config
MIN_RETRY_DELAY_SECONDS: int = 30
MAX_RETRY_DELAY_SECONDS: int = 3600     # 1 hour
BACKOFF_MULTIPLIER: int = 2

# Sau MAX_RETRIES lần failed, KHÔNG reschedule nữa (nhưng vẫn giữ event).
MAX_RETRIES: int = 10

# Thời gian lưu event sent trước khi purge (mặc định 7 ngày)
SENT_RETENTION_DAYS: int = 7


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class QueuedEvent:
    """Một event trong queue (read view)."""

    id: int
    payload: dict[str, Any]
    status: str
    retry_count: int
    created_at: datetime
    sent_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    last_error: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING

    @property
    def is_failed(self) -> bool:
        return self.status == STATUS_FAILED

    @property
    def is_sent(self) -> bool:
        return self.status == STATUS_SENT


@dataclass
class QueueStats:
    """Thống kê queue (cho monitoring)."""

    pending: int = 0
    failed: int = 0
    sent: int = 0
    dead: int = 0       # failed quá MAX_RETRIES lần

    @property
    def total(self) -> int:
        return self.pending + self.failed + self.sent

    def __str__(self) -> str:
        return (
            f"QueueStats(pending={self.pending}, "
            f"failed={self.failed}, dead={self.dead}, "
            f"sent={self.sent}, total={self.total})"
        )


# =========================================================================
# Helpers
# =========================================================================

def _now_utc() -> datetime:
    """Trả về datetime UTC hiện tại (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    """Datetime -> ISO 8601 string (giữ timezone info)."""
    if dt is None:
        return None
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """ISO 8601 string -> datetime (timezone-aware). Parse từ SQLite."""
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Nếu naive (không có tz), assume UTC (cho an toàn với dữ liệu cũ).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_event(row: Any) -> QueuedEvent:
    """Convert sqlite3.Row → QueuedEvent."""
    payload_raw = row["payload"]
    if isinstance(payload_raw, str):
        payload = json.loads(payload_raw)
    else:
        payload = payload_raw
    return QueuedEvent(
        id=row["id"],
        payload=payload,
        status=row["status"],
        retry_count=row["retry_count"],
        created_at=_parse_iso(row["created_at"]) or _now_utc(),
        sent_at=_parse_iso(row["sent_at"]),
        last_attempt_at=_parse_iso(row["last_attempt_at"]),
        next_retry_at=_parse_iso(row["next_retry_at"]),
        last_error=row["last_error"],
    )


# =========================================================================
# Migration: thêm retry columns (idempotent)
# =========================================================================

# Danh sách column cần thêm để hỗ trợ retry mechanism.
# Tất cả đều DEFAULT ... → ALTER TABLE ADD COLUMN an toàn với bảng cũ.
_OPTIONAL_COLUMNS: list[tuple[str, str]] = [
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_attempt_at", "TEXT"),
    ("next_retry_at", "TEXT"),
    ("last_error", "TEXT"),
]


def _migrate_queue_columns(conn) -> None:
    """Thêm cột retry_count/last_attempt_at/next_retry_at/last_error.

    Idempotent: nếu cột đã tồn tại, skip (catch sqlite "duplicate column" error).
    """
    cur = conn.execute("PRAGMA table_info(queue)")
    existing = {row["name"] for row in cur.fetchall()}
    for col_name, col_def in _OPTIONAL_COLUMNS:
        if col_name in existing:
            continue
        try:
            conn.execute(
                f"ALTER TABLE queue ADD COLUMN {col_name} {col_def}"
            )
        except Exception as e:  # noqa: BLE001
            # sqlite raises "duplicate column name" nếu concurrent insert
            msg = str(e).lower()
            if "duplicate" not in msg and "exists" not in msg:
                raise


def _ensure_indexes(conn) -> None:
    """Tạo các index phục vụ retry queries (idempotent)."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_status_retry "
        "ON queue(status, next_retry_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_pending "
        "ON queue(status) WHERE status = 'pending'"
    )


# =========================================================================
# EventQueue – main API
# =========================================================================

class EventQueue:
    """High-level retry queue trên nền SQLite.

    Usage::

        queue = EventQueue(SQLiteStorage(db_path="simulator.db"))
        queue.init()                          # migration + indexes

        # Producer (main loop)
        eid = queue.enqueue(event_dict)

        # Consumer (network worker)
        batch = queue.dequeue_batch(limit=200)
        for ev in batch:
            try:
                response = client.post_batch(...)
                queue.mark_sent([ev.id])
            except NetworkError as e:
                queue.mark_failed(ev.id, str(e))

        # Periodic rescheduler
        queue.reschedule_failed()            # move failed → pending khi đến hạn

    Threading: KHÔNG thread-safe – dùng 1 instance / 1 thread.
    Process: có thể dùng nhiều process, SQLite với WAL mode sẽ serialize.
    """

    def __init__(self, storage: SQLiteStorage) -> None:
        self._storage = storage
        # Lưu db_path để tự mở connection riêng khi cần
        # (vì SQLiteStorage.conn chỉ truy cập được trong `with` block)
        self._db_path = storage.db_path

    def _conn(self) -> sqlite3.Connection:
        """Mở connection mới cho mỗi operation.

        Lý do: SQLiteStorage.conn chỉ truy cập được trong `with` block.
        EventQueue thường xuyên được dùng từ nhiều nơi khác nhau
        (producer thread, network worker, scheduler) – buộc caller
        phải wrap `with SQLiteStorage()` ở mỗi call sẽ rất cồng kềnh.
        Thay vào đó, mỗi method tự mở/đóng connection.
        WAL mode vẫn cho phép concurrent readers + 1 writer.
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def init(self) -> None:
        """Khởi tạo: migrate + create indexes. Idempotent.

        KHÔNG tự động gọi init_schema() – caller phải gọi qua
        ``storage.init_schema()`` trước (Bước 4 đã có).
        """
        conn = self._conn()
        try:
            _migrate_queue_columns(conn)
            _ensure_indexes(conn)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------
    def enqueue(self, event: dict[str, Any]) -> int:
        """Đưa 1 event vào queue (status=pending).

        Args:
            event: dict event đã được tạo bởi generator.

        Returns:
            id (int) – định danh event trong queue.
        """
        created_at = _iso(_now_utc())
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO queue (payload, created_at, status) "
                "VALUES (?, ?, ?)",
                (
                    json.dumps(event, ensure_ascii=False),
                    created_at,
                    STATUS_PENDING,
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def enqueue_batch(self, events: Iterable[dict[str, Any]]) -> list[int]:
        """Đưa nhiều event vào queue cùng lúc (transaction batch).

        Trả về list id tương ứng với từng event (theo thứ tự).
        """
        rows = [
            (
                json.dumps(ev, ensure_ascii=False),
                _iso(_now_utc()),
                STATUS_PENDING,
            )
            for ev in events
        ]
        if not rows:
            return []
        conn = self._conn()
        try:
            # Lưu pre-id
            pre = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM queue").fetchone()["m"]
            conn.executemany(
                "INSERT INTO queue (payload, created_at, status) "
                "VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            # Query lại id của các row vừa insert
            n = len(rows)
            cur = conn.execute(
                "SELECT id FROM queue WHERE id > ? ORDER BY id LIMIT ?",
                (pre, n),
            )
            ids = [r["id"] for r in cur.fetchall()]
            return ids
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------
    def dequeue_batch(
        self,
        limit: int = 100,
        status: str = STATUS_PENDING,
    ) -> list[QueuedEvent]:
        """Lấy ra tối đa ``limit`` event theo status.

        KHÔNG thay đổi status (peek + manual mark_sent/mark_failed).
        Đây là cơ chế phù hợp với retry: caller tự quyết định khi nào
        mark_sent / mark_failed dựa trên kết quả POST.

        Args:
            limit: số event tối đa trả về.
            status: 'pending' | 'sent' | 'failed'.

        Returns:
            list QueuedEvent, sắp xếp theo id tăng dần (FIFO).
        """
        if status not in ALL_STATUSES:
            raise ValueError(
                f"status '{status}' không hợp lệ. "
                f"Phải là 1 trong {sorted(ALL_STATUSES)}"
            )
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT id, payload, status, retry_count, created_at, "
                "sent_at, last_attempt_at, next_retry_at, last_error "
                "FROM queue WHERE status = ? "
                "ORDER BY id LIMIT ?",
                (status, limit),
            )
            return [_row_to_event(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def mark_sent(self, ids: int | list[int]) -> int:
        """Đánh dấu event đã gửi thành công.

        Args:
            ids: 1 id (int) hoặc list id.

        Returns:
            Số row thực sự update (cho verification).
        """
        if isinstance(ids, int):
            ids = [ids]
        if not ids:
            return 0
        sent_at = _iso(_now_utc())
        conn = self._conn()
        try:
            cur = conn.executemany(
                "UPDATE queue SET status = ?, sent_at = ? "
                "WHERE id = ?",
                [(STATUS_SENT, sent_at, i) for i in ids],
            )
            conn.commit()
            return cur.rowcount if cur else len(ids)
        finally:
            conn.close()

    def mark_failed(
        self,
        event_id: int,
        error_message: str,
    ) -> int:
        """Đánh dấu 1 event fail → chuyển sang status='failed'.

        Tính retry_count = retry_count + 1, last_attempt_at = now,
        next_retry_at = now + exponential_backoff(retry_count).

        KHÔNG xoá event. KHÔNG discard. Chỉ chuyển status.

        Args:
            event_id: id event fail.
            error_message: thông tin lỗi (HTTP status, exception, ...).
                           Lưu tóm tắt 200 ký tự đầu để tránh DB phình.

        Returns:
            retry_count mới (1..MAX_RETRIES+).
        """
        now = _now_utc()
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT retry_count FROM queue WHERE id = ?",
                (event_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"Event id={event_id} không tồn tại")
            current_retry = row["retry_count"]
            new_retry = current_retry + 1
            # Exponential backoff
            delay = min(
                MAX_RETRY_DELAY_SECONDS,
                MIN_RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER ** (new_retry - 1)),
            )
            next_retry_at = now + timedelta(seconds=delay)
            error_summary = (
                (error_message[:200] + "...")
                if len(error_message) > 200
                else error_message
            )

            conn.execute(
                "UPDATE queue SET status = ?, retry_count = ?, "
                "last_attempt_at = ?, next_retry_at = ?, last_error = ? "
                "WHERE id = ?",
                (
                    STATUS_FAILED,
                    new_retry,
                    _iso(now),
                    _iso(next_retry_at),
                    error_summary,
                    event_id,
                ),
            )
            conn.commit()
            return new_retry
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Retry scheduler
    # ------------------------------------------------------------------
    def reschedule_failed(
        self,
        max_retry: int = MAX_RETRIES,
    ) -> int:
        """Reschedule các event failed về 'pending' khi đã đến hạn.

        Quy tắc:
            - status = 'failed' AND next_retry_at <= now AND
              retry_count < max_retry → set status = 'pending'.
            - retry_count >= max_retry → giữ nguyên status = 'failed'
              (dead letter, KHÔNG xoá).

        Args:
            max_retry: ngưỡng retry tối đa (mặc định MAX_RETRIES).

        Returns:
            Số event đã reschedule.
        """
        now_iso = _iso(_now_utc())
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE queue SET status = ? "
                "WHERE status = ? AND next_retry_at <= ? "
                "AND retry_count < ?",
                (STATUS_PENDING, STATUS_FAILED, now_iso, max_retry),
            )
            conn.commit()
            return cur.rowcount if cur else 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def purge_sent(self, older_than_days: int = SENT_RETENTION_DAYS) -> int:
        """Xoá các event đã sent quá hạn (mặc định 7 ngày).

        Lưu ý: CHỈ xoá event đã sent. KHÔNG xoá pending/failed.

        Args:
            older_than_days: ngưỡng tuổi tối đa (ngày).

        Returns:
            Số row đã xoá.
        """
        cutoff = _iso(_now_utc() - timedelta(days=older_than_days))
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM queue WHERE status = ? AND sent_at <= ?",
                (STATUS_SENT, cutoff),
            )
            conn.commit()
            return cur.rowcount if cur else 0
        finally:
            conn.close()

    def get_dead_letters(self, max_retry: int = MAX_RETRIES) -> list[QueuedEvent]:
        """Trả về các event đã retry quá nhiều lần (cho operator review).

        KHÔNG xoá. Chỉ truy vấn.

        Args:
            max_retry: ngưỡng retry tối đa.

        Returns:
            list QueuedEvent (status=failed, retry_count >= max_retry).
        """
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT id, payload, status, retry_count, created_at, "
                "sent_at, last_attempt_at, next_retry_at, last_error "
                "FROM queue WHERE status = ? AND retry_count >= ? "
                "ORDER BY id",
                (STATUS_FAILED, max_retry),
            )
            return [_row_to_event(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def stats(self, max_retry: int = MAX_RETRIES) -> QueueStats:
        """Trả về thống kê queue.

        Returns:
            QueueStats(pending, failed, sent, dead).
        """
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT status, "
                "CASE WHEN status = 'failed' AND retry_count >= ? "
                "     THEN 1 ELSE 0 END AS is_dead, "
                "COUNT(*) AS n "
                "FROM queue GROUP BY status, is_dead",
                (max_retry,),
            )
            stats = QueueStats()
            total_failed = 0
            total_dead = 0
            for r in cur.fetchall():
                n = r["n"]
                if r["status"] == STATUS_PENDING:
                    stats.pending = n
                elif r["status"] == STATUS_SENT:
                    stats.sent = n
                elif r["status"] == STATUS_FAILED:
                    total_failed += n
                    if r["is_dead"]:
                        total_dead += n
            stats.failed = total_failed - total_dead
            stats.dead = total_dead
            return stats
        finally:
            conn.close()

    def get(self, event_id: int) -> QueuedEvent | None:
        """Lấy 1 event theo id (cho debug / inspection)."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT id, payload, status, retry_count, created_at, "
                "sent_at, last_attempt_at, next_retry_at, last_error "
                "FROM queue WHERE id = ?",
                (event_id,),
            )
            row = cur.fetchone()
            return _row_to_event(row) if row else None
        finally:
            conn.close()

    def count_by_status(self, status: str) -> int:
        """Đếm số event theo status (helper)."""
        if status not in ALL_STATUSES:
            raise ValueError(
                f"status '{status}' không hợp lệ. "
                f"Phải là 1 trong {sorted(ALL_STATUSES)}"
            )
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT COUNT(*) AS n FROM queue WHERE status = ?",
                (status,),
            )
            return cur.fetchone()["n"]
        finally:
            conn.close()

    def clear_all(self) -> int:
        """Xoá toàn bộ queue – CHỈ dùng cho test/reset.

        Returns:
            Số row đã xoá.
        """
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM queue")
            conn.commit()
            return cur.rowcount if cur else 0
        finally:
            conn.close()


__all__ = [
    "EventQueue",
    "QueuedEvent",
    "QueueStats",
    "STATUS_PENDING",
    "STATUS_SENT",
    "STATUS_FAILED",
    "ALL_STATUSES",
    "MIN_RETRY_DELAY_SECONDS",
    "MAX_RETRY_DELAY_SECONDS",
    "BACKOFF_MULTIPLIER",
    "MAX_RETRIES",
    "SENT_RETENTION_DAYS",
]
