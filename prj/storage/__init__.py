"""Storage package – persistent state cho simulator.

Hiện tại:
    - sqlite: SQLite wrapper + schema (players, sessions, progression_state,
              player_economy, queue)
    - queue:  EventQueue – retry queue với exponential backoff (Bước 16)

Các module khác (raw_writer.py) sẽ bổ sung ở BƯỚC 22.
"""

from __future__ import annotations

from .queue import (
    ALL_STATUSES,
    BACKOFF_MULTIPLIER,
    EventQueue,
    MAX_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    MIN_RETRY_DELAY_SECONDS,
    QueuedEvent,
    QueueStats,
    SENT_RETENTION_DAYS,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
)
from .sqlite import (
    SQLiteStorage,
    _DEFAULT_DB_PATH,
)

__all__ = [
    # SQLite (Bước 4)
    "SQLiteStorage",
    "_DEFAULT_DB_PATH",
    # EventQueue (Bước 16)
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
