"""Event Queue – quản lý retry state + batch metadata.

Theo BƯỚC 21 INSTRUCTION.md:

    Batch → POST
      ├── 200 OK → Mark sent
      └── FAIL → Retry Queue → Retry (exponential backoff, giới hạn max retry)

> **Không xoá raw batch trước khi request thành công.**
> Đây là rule data quality quan trọng.

Module này cung cấp:

Queue schema mở rộng (Bước 16 cũ):

    ALTER TABLE queue ADD COLUMN retry_count    INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE queue ADD COLUMN next_retry_at  TEXT;   -- ISO timestamp
    ALTER TABLE queue ADD COLUMN last_error     TEXT;   -- error message
    ALTER TABLE queue ADD COLUMN last_attempt_at TEXT;  -- ISO timestamp

Batch metadata table (mới cho Bước 21):

    CREATE TABLE IF NOT EXISTS batch_retry (
        batch_id       TEXT PRIMARY KEY,
        retry_count    INTEGER NOT NULL DEFAULT 0,
        max_retries    INTEGER NOT NULL,
        next_retry_at  TEXT,   -- ISO timestamp, NULL = done
        last_error     TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
    );

RetryQueue:

    add_failed_batch(batch_id, events, error, max_retries)
      → lưu batch metadata
      → KHÔNG xoá events (giữ để replay)
      → schedule next retry với exponential backoff

    get_retryable_batches(now) → list[RetryBatch]
      → lấy các batch đã đến lúc retry

    mark_batch_sent(batch_id) → xoá khỏi retry queue
    mark_batch_exhausted(batch_id) → chuyển events → queue.failed
    mark_event_sent(event_id) → đánh dấu 1 event đã gửi
    mark_event_failed(event_id, error, next_retry_at) → đánh dấu thử lại
    mark_retry_exhausted(event_id) → đánh dấu permanent fail
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from storage.sqlite import SQLiteStorage

# =========================================================================
# RetryPolicy
# =========================================================================

@dataclass(frozen=True)
class RetryPolicy:
    """Cấu hình retry behavior.

    Attributes:
        max_retries: số lần retry tối đa (default 5).
                     0 = không retry.
        base_delay_seconds: delay ban đầu giữa 2 lần retry (default 1.0s).
        max_delay_seconds: delay tối đa giữa 2 lần retry (default 60.0s).
        jitter_factor: jitter factor ∈ [0, 1). 0.1 = ±10%%.
                       Dùng để tránh thundering herd.
                       None = không có jitter (deterministic).
    """

    max_retries: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    jitter_factor: float | None = 0.1

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(f"max_retries phải >= 0, nhận {self.max_retries}")
        if self.base_delay_seconds < 0:
            raise ValueError(
                f"base_delay_seconds phải >= 0, nhận {self.base_delay_seconds}"
            )
        if self.max_delay_seconds <= 0:
            raise ValueError(
                f"max_delay_seconds phải > 0, nhận {self.max_delay_seconds}"
            )
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError(
                f"base_delay_seconds ({self.base_delay_seconds}) phải <= "
                f"max_delay_seconds ({self.max_delay_seconds})"
            )
        if self.jitter_factor is not None and not (0 <= self.jitter_factor < 1):
            raise ValueError(
                f"jitter_factor phải ∈ [0, 1), nhận {self.jitter_factor}"
            )

    def compute_delay(self, retry_count: int) -> float:
        """Tính delay cho retry thứ ``retry_count`` (1-indexed).

        Exponential backoff::

            delay = min(base_delay * 2^(retry_count-1), max_delay)
            jitter = delay * random(1 - jitter_factor, 1 + jitter_factor)

        Args:
            retry_count: số lần retry đã thử (1 = lần đầu tiên).

        Returns:
            delay tính bằng giây (float).
        """
        if retry_count <= 0:
            return 0.0
        raw_delay = self.base_delay_seconds * (2 ** (retry_count - 1))
        delay = min(raw_delay, self.max_delay_seconds)
        if self.jitter_factor is not None:
            lo = 1.0 - self.jitter_factor
            hi = 1.0 + self.jitter_factor
            delay *= random.uniform(lo, hi)
        return delay

    def is_exhausted(self, retry_count: int) -> bool:
        """True nếu đã retry quá max_retries.

        Với max_retries=0: ngay lập tức exhausted.
        Với max_retries=N:
            - retry_count <= N → chưa exhausted (còn retry)
            - retry_count > N → exhausted
        """
        return retry_count > self.max_retries

    def should_retry(self, retry_count: int) -> bool:
        """True nếu nên retry (chưa exhaust)."""
        return not self.is_exhausted(retry_count)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class RetryBatch:
    """Một batch đang trong retry queue.

    Attributes:
        batch_id: định danh batch.
        events: list event cần gửi lại.
        retry_count: số lần đã retry (1 = đã thử 1 lần).
        max_retries: giới hạn retry.
        last_error: error message từ lần retry trước.
        next_retry_at: unix timestamp epoch.
        created_at: iso timestamp tạo batch.
    """

    batch_id: str
    events: list[dict[str, Any]]
    retry_count: int
    max_retries: int
    last_error: str | None
    next_retry_at: float
    created_at: str

    @property
    def attempts_remaining(self) -> int:
        """Số lần retry còn lại."""
        return max(0, self.max_retries - self.retry_count)

    @property
    def is_exhausted(self) -> bool:
        """True nếu đã retry hết max_retries."""
        return self.retry_count >= self.max_retries


@dataclass
class RetryQueueStats:
    """Thống kê retry queue."""

    total_batches: int = 0
    retryable_batches: int = 0
    exhausted_batches: int = 0
    total_events_retried: int = 0
    total_events_exhausted: int = 0

    def __str__(self) -> str:
        return (
            f"RetryQueueStats(total_batches={self.total_batches}, "
            f"retryable={self.retryable_batches}, "
            f"exhausted={self.exhausted_batches}, "
            f"events_retried={self.total_events_retried}, "
            f"events_exhausted={self.total_events_exhausted})"
        )


# =========================================================================
# RetryQueue – in-memory batch-level retry
# =========================================================================

class RetryQueue:
    """Queue quản lý retry ở batch level.

    Design rationale: Retry ở batch level vì:
        1. ``send_events()`` nhận Batch (list events), không phải từng event.
        2. Nếu 1 event fail, toàn batch nên được retry cùng nhau.
        3. Event table (queue) vẫn giữ pending events cho trường hợp
           khác (non-batch flow hoặc recovery sau crash).

    Usage::

        queue = RetryQueue(
            storage=SQLiteStorage(),
            retry_policy=RetryPolicy(max_retries=5),
        )
        queue.start()

        # Khi batch fail:
        try:
            result = client.send_events(batch.events, batch_id=batch_id)
        except GameAnalyticsServerError as e:
            queue.add_failed_batch(batch_id, batch.events, str(e))

        # Trong retry loop:
        for retry_batch in queue.get_retryable_batches():
            try:
                client.send_events(retry_batch.events,
                                   batch_id=retry_batch.batch_id)
                queue.mark_batch_sent(retry_batch.batch_id)
            except GameAnalyticsServerError:
                queue.add_failed_batch(retry_batch.batch_id,
                                       retry_batch.events, new_error)

        queue.stop()
    """

    # Migrations: thứ tự quan trọng (ALTER TABLE cần chạy đúng)
    _QUEUE_MIGRATIONS: list[str] = [
        # ALTER TABLE có thể chạy nhiều lần (IF NOT EXISTS an toàn)
        "ALTER TABLE queue ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE queue ADD COLUMN next_retry_at TEXT",
        "ALTER TABLE queue ADD COLUMN last_error TEXT",
        "ALTER TABLE queue ADD COLUMN last_attempt_at TEXT",
    ]

    _BATCH_RETRY_TABLE: str = """
    CREATE TABLE IF NOT EXISTS batch_retry (
        batch_id       TEXT PRIMARY KEY,
        retry_count    INTEGER NOT NULL DEFAULT 0,
        max_retries    INTEGER NOT NULL DEFAULT 5,
        next_retry_at  TEXT,
        last_error     TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    );
    """
    _BATCH_RETRY_INDEX: str = (
        "CREATE INDEX IF NOT EXISTS idx_batch_retry_next "
        "ON batch_retry(next_retry_at)"
    )

    def __init__(
        self,
        storage: SQLiteStorage,
        retry_policy: RetryPolicy | None = None,
        time_source: Callable[[], float] | None = None,
        id_source: Callable[[], str] | None = None,
    ) -> None:
        """Khởi tạo RetryQueue.

        Args:
            storage: SQLiteStorage để persist retry state.
            retry_policy: cấu hình retry. Mặc định RetryPolicy().
            time_source: callable trả float unix ts (cho test).
            id_source: callable trả string (cho test).
        """
        self._storage = storage
        self._policy = retry_policy or RetryPolicy()
        self._time_source = time_source or time.time
        self._id_source = id_source or (lambda: f"batch_{int(time.time()*1000)}_{random.randint(0,9999):04d}")

        # In-memory cache: batch_id → RetryBatch (chỉ retryable)
        self._retryable: dict[str, RetryBatch] = {}
        # Exhausted: batch_id → RetryBatch (đã hết retry, đợi xử lý)
        self._exhausted: dict[str, RetryBatch] = {}

        # Tự động migrate khi init
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        """Migrate queue schema + tạo batch_retry table.

        Tự động mở storage connection nếu chưa mở.
        LƯU: gọi init_schema() trước để đảm bảo schema queue đã tồn tại
        (ALTER TABLE queue cần queue đã có).
        """
        storage_conn = getattr(self._storage, "_conn", None)
        if storage_conn is not None:
            if not self._queue_table_exists(storage_conn):
                self._storage.init_schema()
            self._run_migrations(storage_conn)
        else:
            with self._storage as _s:
                if not self._queue_table_exists(_s.conn):
                    _s.init_schema()
                self._run_migrations(_s.conn)

    def _queue_table_exists(self, conn) -> bool:
        """Kiểm tra bảng queue đã tồn tại chưa."""
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='queue'"
        )
        return cur.fetchone() is not None

    def _with_conn(self, fn: Callable[[Any], _T]) -> _T:
        """Execute ``fn(conn)`` với storage connection đã mở.

        Nếu storage đã mở (context manager đang active) thì dùng lại.
        Nếu chưa mở thì tự mở.
        """
        storage_conn = getattr(self._storage, "_conn", None)
        if storage_conn is not None:
            return fn(storage_conn)
        with self._storage as _s:
            return fn(_s.conn)

    def _run_migrations(self, conn) -> None:
        """Chạy các migration SQL."""
        # Migrate queue table
        for sql in self._QUEUE_MIGRATIONS:
            try:
                conn.execute(sql)
            except Exception:  # noqa: BLE001
                pass

        # Tạo batch_retry table
        conn.execute(self._BATCH_RETRY_TABLE)
        conn.execute(self._BATCH_RETRY_INDEX)

    # ------------------------------------------------------------------
    # Add failed batch
    # ------------------------------------------------------------------
    def add_failed_batch(
        self,
        batch_id: str,
        events: list[dict[str, Any]],
        error: str,
    ) -> bool:
        """Thêm batch vào retry queue.

        Gọi khi ``send_events()`` throw ``GameAnalyticsServerError``
        hoặc network error.

        **KHÔNG xoá events** — giữ lại để replay khi retry.

        Args:
            batch_id: định danh batch.
            events: list event trong batch.
            error: error message.

        Returns:
            True nếu batch được thêm vào retry queue.
            False nếu batch đã exhausted (không thêm nữa).
        """
        if batch_id in self._retryable:
            # Đã có trong retryable → cập nhật retry count
            batch = self._retryable[batch_id]
            new_count = batch.retry_count + 1
            return self._update_retry_batch(batch_id, new_count, error)

        # Batch mới
        now = self._time_source()
        retry_count = 1
        if not self._policy.should_retry(retry_count):
            # Không retry được → exhausted
            batch = RetryBatch(
                batch_id=batch_id,
                events=events,
                retry_count=retry_count,
                max_retries=self._policy.max_retries,
                last_error=error,
                next_retry_at=0.0,
                created_at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            )
            self._exhausted[batch_id] = batch
            self._persist_batch(batch)
            return False

        # Tính next_retry_at
        delay = self._policy.compute_delay(retry_count)
        next_retry_at = now + delay

        batch = RetryBatch(
            batch_id=batch_id,
            events=events,
            retry_count=retry_count,
            max_retries=self._policy.max_retries,
            last_error=error,
            next_retry_at=next_retry_at,
            created_at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        )

        self._retryable[batch_id] = batch
        self._persist_batch(batch)
        return True

    def _update_retry_batch(
        self,
        batch_id: str,
        new_retry_count: int,
        error: str,
    ) -> bool:
        """Cập nhật retry count của batch đã có trong queue."""
        batch = self._retryable[batch_id]
        if not self._policy.should_retry(new_retry_count):
            # Exhausted → chuyển sang exhausted dict
            del self._retryable[batch_id]
            exhausted = RetryBatch(
                batch_id=batch.batch_id,
                events=batch.events,
                retry_count=new_retry_count,
                max_retries=batch.max_retries,
                last_error=error,
                next_retry_at=0.0,
                created_at=batch.created_at,
            )
            self._exhausted[batch_id] = exhausted
            self._persist_batch(exhausted)
            return False

        # Tiếp tục retry
        delay = self._policy.compute_delay(new_retry_count)
        now = self._time_source()
        updated = RetryBatch(
            batch_id=batch.batch_id,
            events=batch.events,
            retry_count=new_retry_count,
            max_retries=batch.max_retries,
            last_error=error,
            next_retry_at=now + delay,
            created_at=batch.created_at,
        )
        self._retryable[batch_id] = updated
        self._persist_batch(updated)
        return True

    # ------------------------------------------------------------------
    # Get retryable batches
    # ------------------------------------------------------------------
    def get_retryable_batches(self) -> list[RetryBatch]:
        """Lấy các batch đã đến lúc retry.

        Returns:
            List RetryBatch sắp xếp theo next_retry_at (cũ nhất trước).
        """
        now = self._time_source()
        ready: list[RetryBatch] = []

        # Lấy từ in-memory cache
        for batch_id, batch in list(self._retryable.items()):
            if batch.next_retry_at <= now:
                ready.append(batch)

        # Sắp xếp theo next_retry_at
        ready.sort(key=lambda b: b.next_retry_at)
        return ready

    # ------------------------------------------------------------------
    # Mark batch done
    # ------------------------------------------------------------------
    def mark_batch_sent(self, batch_id: str) -> None:
        """Đánh dấu batch đã gửi thành công.

        Xoá khỏi retry queue. Các event trong batch đã được
        đánh dấu sent qua ``mark_event_sent()`` ở caller.

        Args:
            batch_id: batch đã thành công.
        """
        # Xoá khỏi in-memory
        self._retryable.pop(batch_id, None)
        self._exhausted.pop(batch_id, None)
        # Xoá khỏi DB
        self._with_conn(
            lambda conn: conn.execute(
                "DELETE FROM batch_retry WHERE batch_id = ?", (batch_id,),
            )
        )

    def mark_batch_exhausted(self, batch_id: str) -> None:
        """Chuyển batch thành permanently failed.

        Gọi khi đã retry hết max_retries. Caller (hoặc hệ thống
        monitoring) sẽ xử lý các events trong batch.

        Args:
            batch_id: batch đã exhausted.
        """
        batch = self._retryable.pop(batch_id, None)
        if batch is None:
            batch = self._exhausted.get(batch_id)

        if batch is not None:
            exhausted = RetryBatch(
                batch_id=batch.batch_id,
                events=batch.events,
                retry_count=batch.retry_count,
                max_retries=batch.max_retries,
                last_error=batch.last_error,
                next_retry_at=0.0,
                created_at=batch.created_at,
            )
            self._exhausted[batch_id] = exhausted
            self._persist_batch(exhausted)

    def get_exhausted_batches(self) -> list[RetryBatch]:
        """Lấy các batch đã permanently failed."""
        return list(self._exhausted.values())

    def clear_exhausted(self) -> None:
        """Xoá exhausted batches (caller đã xử lý xong)."""
        for batch_id in list(self._exhausted.keys()):
            self.mark_batch_sent(batch_id)  # dùng chung logic xoá

    # ------------------------------------------------------------------
    # Event-level helpers (tích hợp với queue table)
    # ------------------------------------------------------------------
    def mark_event_sent(self, event_id: int) -> None:
        """Đánh dấu 1 event là đã gửi (wrapper)."""
        self._storage.mark_event_sent(event_id)

    def mark_event_failed(
        self,
        event_id: int,
        error: str,
        next_retry_at: datetime | None = None,
    ) -> None:
        """Đánh dấu 1 event retry fail (wrapper).

        Tăng retry_count trong queue table.

        Args:
            event_id: event id trong queue.
            error: error message.
            next_retry_at: datetime tính retry tiếp theo.
                          None = không retry nữa.
        """
        retry_at_str = (
            next_retry_at.isoformat() if next_retry_at else None
        )
        self._storage.mark_event_failed(event_id, error, retry_at_str)

    def mark_retry_exhausted(self, event_id: int, error: str) -> None:
        """Đánh dấu event là permanently failed (wrapper)."""
        self._storage.mark_retry_exhausted(event_id, error)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _persist_batch(self, batch: RetryBatch) -> None:
        """Lưu batch metadata vào batch_retry table."""
        now_iso = datetime.fromtimestamp(
            self._time_source(), tz=timezone.utc,
        ).isoformat()
        next_iso = (
            datetime.fromtimestamp(batch.next_retry_at, tz=timezone.utc).isoformat()
            if batch.next_retry_at > 0 else None
        )

        def _do(conn):
            conn.execute(
                """
                INSERT INTO batch_retry
                    (batch_id, retry_count, max_retries, next_retry_at,
                     last_error, created_at, updated_at)
                VALUES
                    (:batch_id, :retry_count, :max_retries, :next_retry_at,
                     :last_error, :created_at, :updated_at)
                ON CONFLICT(batch_id) DO UPDATE SET
                    retry_count  = excluded.retry_count,
                    next_retry_at = excluded.next_retry_at,
                    last_error   = excluded.last_error,
                    updated_at   = excluded.updated_at
                """,
                {
                    "batch_id": batch.batch_id,
                    "retry_count": batch.retry_count,
                    "max_retries": batch.max_retries,
                    "next_retry_at": next_iso,
                    "last_error": batch.last_error,
                    "created_at": batch.created_at,
                    "updated_at": now_iso,
                },
            )

        self._with_conn(_do)

    def _delete_batch(self, batch_id: str) -> None:
        """Xoá batch metadata."""
        self._with_conn(lambda conn: conn.execute(
            "DELETE FROM batch_retry WHERE batch_id = ?",
            (batch_id,),
        ))

    # ------------------------------------------------------------------
    # Load from DB (khôi phục state sau restart)
    # ------------------------------------------------------------------
    def load_from_db(self) -> None:
        """Load retry state từ DB vào memory (sau restart)."""
        def _load(conn):
            cur = conn.execute(
                "SELECT batch_id, retry_count, max_retries, next_retry_at, "
                "last_error, created_at FROM batch_retry"
            )
            now = self._time_source()
            for row in cur.fetchall():
                batch_id = row["batch_id"]
                retry_count = row["retry_count"]
                max_retries = row["max_retries"]
                next_retry_at_raw = row["next_retry_at"]
                next_retry_at = (
                    datetime.fromisoformat(next_retry_at_raw).timestamp()
                    if next_retry_at_raw else 0.0
                )
                last_error = row["last_error"]
                created_at = row["created_at"]

                if next_retry_at_raw is None or retry_count >= max_retries:
                    self._exhausted[batch_id] = RetryBatch(
                        batch_id=batch_id,
                        events=[],
                        retry_count=retry_count,
                        max_retries=max_retries,
                        last_error=last_error,
                        next_retry_at=0.0,
                        created_at=created_at,
                    )
                else:
                    self._retryable[batch_id] = RetryBatch(
                        batch_id=batch_id,
                        events=[],
                        retry_count=retry_count,
                        max_retries=max_retries,
                        last_error=last_error,
                        next_retry_at=next_retry_at,
                        created_at=created_at,
                    )
        self._with_conn(_load)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    @property
    def stats(self) -> RetryQueueStats:
        """Thống kê retry queue."""
        return RetryQueueStats(
            total_batches=len(self._retryable) + len(self._exhausted),
            retryable_batches=len(self._retryable),
            exhausted_batches=len(self._exhausted),
        )


# =========================================================================
# RetryableGameAnalyticsClient – high-level wrapper
# =========================================================================

class RetryableGameAnalyticsClient:
    """GameAnalyticsClient có tích hợp retry.

    Wraps ``GameAnalyticsClient`` và tự động retry khi gặp
    ``GameAnalyticsServerError`` hoặc network error.

    Usage::

        from gameanalytics import GameAnalyticsClient
        from gameanalytics.retry import (
            RetryableGameAnalyticsClient,
            RetryPolicy,
        )

        base_client = GameAnalyticsClient.from_env()
        client = RetryableGameAnalyticsClient(
            base_client=base_client,
            retry_policy=RetryPolicy(max_retries=5, base_delay=1.0),
        )

        # Init
        client.init()

        # Send (tự retry nếu 5xx)
        result = client.send_events(events)
    """

    def __init__(
        self,
        base_client: Any,  # GameAnalyticsClient
        retry_policy: RetryPolicy | None = None,
        retry_queue: RetryQueue | None = None,
        storage: SQLiteStorage | None = None,
    ) -> None:
        """Khởi tạo RetryableClient.

        Args:
            base_client: GameAnalyticsClient đã khởi tạo.
            retry_policy: cấu hình retry. Mặc định RetryPolicy().
            retry_queue: RetryQueue. Nếu None, tự tạo với storage.
            storage: SQLiteStorage cho retry queue. Bắt buộc nếu
                     retry_queue=None.
        """
        self._base = base_client
        self._policy = retry_policy or RetryPolicy()
        if retry_queue is not None:
            self._queue = retry_queue
        else:
            if storage is None:
                storage = SQLiteStorage()
            self._queue = RetryQueue(storage=storage, retry_policy=self._policy)

    @property
    def base_client(self) -> Any:
        return self._base

    @property
    def retry_queue(self) -> RetryQueue:
        return self._queue

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._policy

    def init(self) -> Any:
        """Gọi init của base client."""
        return self._base.init()

    def send_events(
        self,
        events: list[dict[str, Any]],
        batch_id: str | None = None,
        use_gzip: bool = True,
    ) -> Any:  # SendResult
        """Gửi batch, tự retry nếu gặp retryable error.

        Retry chỉ áp dụng cho:
            - GameAnalyticsServerError (5xx)
            - GameAnalyticsError với status_code=None (network error)

        KHÔNG retry cho:
            - GameAnalyticsAuthError (401/403)
            - GameAnalyticsPayloadTooLargeError (413)
            - GameAnalyticsError với 4xx khác

        Args:
            events: list event.
            batch_id: batch id (auto-generate nếu None).
            use_gzip: dùng gzip mode.

        Returns:
            SendResult (thành công).

        Raises:
            GameAnalyticsError: sau khi exhaust hoặc lỗi fatal.
        """
        _batch_id = batch_id or self._base._id_generator()
        current_retry_count = 0
        last_error: str = ""
        # Lazy import to avoid circular
        from gameanalytics.client import GameAnalyticsError

        while True:
            try:
                result = self._base.send_events(
                    events,
                    batch_id=_batch_id,
                    use_gzip=use_gzip,
                    retry_count=current_retry_count,
                )
                # Thành công → xoá khỏi retry queue
                self._queue.mark_batch_sent(_batch_id)
                return result

            except GameAnalyticsError as e:
                last_error = str(e)
                # Kiểm tra loại error
                if self._is_retryable(e):
                    current_retry_count += 1
                    if self._policy.should_retry(current_retry_count):
                        # Thêm vào queue + chờ backoff
                        self._queue.add_failed_batch(
                            _batch_id, events, last_error,
                        )
                        # Chờ backoff
                        delay = self._policy.compute_delay(current_retry_count)
                        time.sleep(delay)
                        continue
                    else:
                        # Exhausted
                        self._queue.mark_batch_exhausted(_batch_id)
                        raise
                else:
                    # Fatal error
                    self._queue.mark_batch_sent(_batch_id)  # clean up
                    raise

    @staticmethod
    def _is_retryable(error: GameAnalyticsError) -> bool:
        """True nếu error nên retry."""
        from gameanalytics.client import (
            GameAnalyticsAuthError,
            GameAnalyticsPayloadTooLargeError,
            GameAnalyticsServerError,
        )
        if isinstance(error, GameAnalyticsAuthError):
            return False
        if isinstance(error, GameAnalyticsPayloadTooLargeError):
            return False
        if isinstance(error, GameAnalyticsServerError):
            return True
        if error.status_code is None:
            # Network error → retry
            return True
        # 4xx khác → không retry (theo HTTP semantics)
        return False


# =========================================================================
# Factory helpers
# =========================================================================

def create_retry_client(
    game_key: str | None = None,
    secret_key: str | None = None,
    environment: str | None = None,
    base_url: str | None = None,
    retry_policy: RetryPolicy | None = None,
    storage: SQLiteStorage | None = None,
) -> RetryableGameAnalyticsClient:
    """Factory helper: tạo RetryableClient từ credentials.

    Args:
        game_key: GAME_KEY. None = load từ .env.
        secret_key: SECRET_KEY. None = load từ .env.
        environment: sandbox/production. None = load từ .env.
        base_url: override base URL.
        retry_policy: retry config.
        storage: SQLiteStorage.

    Returns:
        RetryableGameAnalyticsClient.
    """
    from gameanalytics.client import GameAnalyticsClient
    from config.credentials import get_credentials

    if game_key is None:
        creds = get_credentials()
        game_key = creds.game_key
        secret_key = creds.secret_key
        environment = environment or creds.environment

    base_client = GameAnalyticsClient(
        game_key=game_key,
        secret_key=secret_key,
        environment=environment or "sandbox",
        base_url=base_url,
    )
    return RetryableGameAnalyticsClient(
        base_client=base_client,
        retry_policy=retry_policy,
        storage=storage,
    )


__all__ = [
    "RetryBatch",
    "RetryPolicy",
    "RetryQueue",
    "RetryQueueStats",
    "RetryableGameAnalyticsClient",
    "create_retry_client",
]
