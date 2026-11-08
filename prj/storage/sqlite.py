"""SQLite storage – persistent state cho simulator.

Theo BƯỚC 4 trong INSTRUCTION.md (section 32 của README):

Các bảng:
    - players         (toàn bộ field của Player Model)
    - sessions
    - progression_state
    - player_economy
    - queue           (event queue – dùng ở Bước 16)

Các field bắt buộc persistent:
    session_num, transaction_num, current_level,
    coins, gems, last_session, churn_status

File DB mặc định: ``simulator.db`` ở project root (cùng folder với .env).
File này đã được ignore trong .gitignore.

Module cung cấp:
    - SQLiteStorage: context manager-friendly wrapper quanh sqlite3.Connection
    - init_schema(): tạo các bảng nếu chưa tồn tại (idempotent)
    - save_players / load_players: bulk insert + load toàn bộ
    - update_player_state: cập nhật counter & economy sau mỗi session
    - save_session / load_sessions: lưu và đọc session
    - update_progression / update_economy: cập nhật progression/economy
    - enqueue_event / dequeue_batch: thêm / lấy event từ queue
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from models import Player


# === Đường dẫn DB ===
# Mặc định: <project_root>/simulator.db
# Cho phép override qua biến môi trường SIMULATOR_DB (tiện cho test).
_DEFAULT_DB_PATH = Path(
    os.environ.get(
        "SIMULATOR_DB",
        str(Path(__file__).resolve().parent.parent / "simulator.db"),
    )
)


# =========================================================================
# SCHEMA
# =========================================================================
# Mỗi bảng được tạo bằng IF NOT EXISTS để init_schema() là idempotent.
# Lưu ý: dùng TEXT cho datetime (ISO 8601) để tránh phụ thuộc SQLite built-in
# datetime functions; INTEGER cho Unix timestamp; INTEGER cho boolean (0/1).
# =========================================================================

_SCHEMA_SQL: list[str] = [
    # ----------------------------------------------------------------------
    # players – bảng tổng, lưu toàn bộ field của Player model
    # ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS players (
        user_id              TEXT PRIMARY KEY,
        country              TEXT NOT NULL,
        platform             TEXT NOT NULL,                -- android | ios
        device               TEXT NOT NULL,
        manufacturer         TEXT NOT NULL,
        os_version           TEXT NOT NULL,

        install_date         TEXT NOT NULL,                -- ISO 8601
        acquisition_source   TEXT NOT NULL,
        campaign_id          TEXT,                         -- NULL cho organic

        player_segment       TEXT NOT NULL,                -- payer segment
        engagement_segment   TEXT NOT NULL,
        churn_segment        TEXT NOT NULL,

        current_level        INTEGER NOT NULL DEFAULT 1,
        current_world        INTEGER NOT NULL DEFAULT 1,
        current_attempt_num  INTEGER NOT NULL DEFAULT 1,

        coins                INTEGER NOT NULL DEFAULT 0,
        gems                 INTEGER NOT NULL DEFAULT 50,
        energy               INTEGER NOT NULL DEFAULT 30,
        lives                INTEGER NOT NULL DEFAULT 5,
        stars                INTEGER NOT NULL DEFAULT 0,
        tickets              INTEGER NOT NULL DEFAULT 1,

        session_count        INTEGER NOT NULL DEFAULT 0,
        transaction_num      INTEGER NOT NULL DEFAULT 0,
        lifetime_revenue     INTEGER NOT NULL DEFAULT 0,   -- minor unit

        last_session_date    TEXT,                         -- ISO 8601, NULL = chưa chơi
        churn_status         TEXT NOT NULL DEFAULT 'active',

        is_new_install       INTEGER NOT NULL DEFAULT 1,   -- 0/1 boolean
        created_at           TEXT NOT NULL,
        updated_at           TEXT NOT NULL
    );
    """,
    # Index phụ cho truy vấn theo segment / country
    "CREATE INDEX IF NOT EXISTS idx_players_segment   ON players(player_segment);",
    "CREATE INDEX IF NOT EXISTS idx_players_engagement ON players(engagement_segment);",
    "CREATE INDEX IF NOT EXISTS idx_players_country    ON players(country);",
    "CREATE INDEX IF NOT EXISTS idx_players_install    ON players(install_date);",

    # ----------------------------------------------------------------------
    # sessions – lưu mỗi session một bản ghi
    # ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id           TEXT PRIMARY KEY,             -- UUID v4
        user_id              TEXT NOT NULL,
        session_num          INTEGER NOT NULL,             -- 1, 2, 3 ...
        session_start_ts     INTEGER NOT NULL,             -- unix ts (giây)
        session_end_ts       INTEGER,                      -- NULL nếu chưa end
        duration_seconds     INTEGER NOT NULL DEFAULT 0,
        connection_type      TEXT NOT NULL,                -- wifi | wwan
        engagement_segment   TEXT NOT NULL,                -- snapshot tại session
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user     ON sessions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_start    ON sessions(session_start_ts);",

    # ----------------------------------------------------------------------
    # progression_state – level progression của player (1 row / player)
    # ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS progression_state (
        user_id              TEXT PRIMARY KEY,
        current_level        INTEGER NOT NULL,
        current_world        INTEGER NOT NULL,
        current_attempt_num  INTEGER NOT NULL,
        updated_at           TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    );
    """,

    # ----------------------------------------------------------------------
    # player_economy – balance hiện tại (1 row / player)
    # ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS player_economy (
        user_id              TEXT PRIMARY KEY,
        coins                INTEGER NOT NULL,
        gems                 INTEGER NOT NULL,
        energy               INTEGER NOT NULL,
        lives                INTEGER NOT NULL,
        stars                INTEGER NOT NULL,
        tickets              INTEGER NOT NULL,
        updated_at           TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES players(user_id)
    );
    """,

    # ----------------------------------------------------------------------
    # queue – event queue, dùng cho Bước 16
    # ----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS queue (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        payload              TEXT NOT NULL,                -- JSON string
        created_at           TEXT NOT NULL,
        sent_at              TEXT,                         -- NULL = chưa gửi
        status               TEXT NOT NULL DEFAULT 'pending'  -- pending | sent | failed
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_queue_status  ON queue(status);",
    "CREATE INDEX IF NOT EXISTS idx_queue_created ON queue(created_at);",
]


# =========================================================================
# SQLiteStorage – class bao bọc sqlite3.Connection
# =========================================================================

class SQLiteStorage:
    """Wrapper nhẹ quanh sqlite3.Connection.

    Quy ước:
        - Mỗi instance gắn với một file DB (mặc định ``simulator.db`` ở project root).
        - Dùng được như context manager:
              with SQLiteStorage() as db:
                  db.save_players(players)
        - Auto commit khi thoát context, rollback nếu có exception.
        - Foreign key được bật (PRAGMA foreign_keys = ON).
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "SQLiteStorage":
        # Đảm bảo thư mục cha tồn tại
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        # Bật foreign key constraint (mặc định SQLite tắt)
        self._conn.execute("PRAGMA foreign_keys = ON")
        # journal_mode=WAL giúp ghi/đọc song song tốt hơn
        self._conn.execute("PRAGMA journal_mode = WAL")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Raw connection (cho transaction phức tạp hoặc custom query)
    # ------------------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "SQLiteStorage phải được dùng trong context manager 'with'"
            )
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Tường minh transaction (commit nếu không exception)."""
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        """Tạo tất cả bảng / index. Idempotent: chạy nhiều lần vẫn an toàn."""
        with self.transaction() as conn:
            for stmt in _SCHEMA_SQL:
                conn.execute(stmt)

    def drop_all(self) -> None:
        """Xoá toàn bộ bảng – chỉ dùng cho test / reset."""
        with self.transaction() as conn:
            for table in ("queue", "player_economy", "progression_state",
                          "sessions", "players"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")

    # ------------------------------------------------------------------
    # Players
    # ------------------------------------------------------------------
    def save_players(self, players: list[Player]) -> None:
        """Bulk insert / replace toàn bộ player.

        Dùng INSERT OR REPLACE (UPSERT) theo user_id – nếu user_id đã tồn tại
        thì cập nhật, đảm bảo idempotent cho việc sinh lại tập player.
        """
        rows = [_player_to_row(p) for p in players]
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO players (
                    user_id, country, platform, device, manufacturer, os_version,
                    install_date, acquisition_source, campaign_id,
                    player_segment, engagement_segment, churn_segment,
                    current_level, current_world, current_attempt_num,
                    coins, gems, energy, lives, stars, tickets,
                    session_count, transaction_num, lifetime_revenue,
                    last_session_date, churn_status,
                    is_new_install, created_at, updated_at
                ) VALUES (
                    :user_id, :country, :platform, :device, :manufacturer, :os_version,
                    :install_date, :acquisition_source, :campaign_id,
                    :player_segment, :engagement_segment, :churn_segment,
                    :current_level, :current_world, :current_attempt_num,
                    :coins, :gems, :energy, :lives, :stars, :tickets,
                    :session_count, :transaction_num, :lifetime_revenue,
                    :last_session_date, :churn_status,
                    :is_new_install, :created_at, :updated_at
                )
                """,
                rows,
            )

    def load_players(self) -> list[Player]:
        """Đọc toàn bộ player ra list[Player], sắp xếp theo user_id."""
        with self.conn:
            cur = self.conn.execute("SELECT * FROM players ORDER BY user_id")
        return [_row_to_player(r) for r in cur.fetchall()]

    def get_player(self, user_id: str) -> Player | None:
        """Đọc 1 player theo user_id, trả None nếu không tồn tại."""
        with self.conn:
            cur = self.conn.execute("SELECT * FROM players WHERE user_id = ?",
                                    (user_id,))
        row = cur.fetchone()
        return _row_to_player(row) if row else None

    def update_player_state(self, player: Player) -> None:
        """Cập nhật các field persistent sau một session.

        Theo INSTRUCTION section 4: session_num (session_count), transaction_num,
        current_level, coins, gems, last_session, churn_status.
        """
        player.touch()
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE players
                SET current_level       = :current_level,
                    current_world       = :current_world,
                    current_attempt_num = :current_attempt_num,
                    coins               = :coins,
                    gems                = :gems,
                    energy              = :energy,
                    lives               = :lives,
                    stars               = :stars,
                    tickets             = :tickets,
                    session_count       = :session_count,
                    transaction_num     = :transaction_num,
                    lifetime_revenue    = :lifetime_revenue,
                    last_session_date   = :last_session_date,
                    churn_status        = :churn_status,
                    is_new_install      = :is_new_install,
                    updated_at          = :updated_at
                WHERE user_id = :user_id
                """,
                _player_to_row(player),
            )

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def save_session(
        self,
        session_id: str,
        user_id: str,
        session_num: int,
        session_start_ts: int,
        session_end_ts: int | None,
        duration_seconds: int,
        connection_type: str,
        engagement_segment: str,
    ) -> None:
        """Lưu 1 session (INSERT OR REPLACE theo session_id)."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    session_id, user_id, session_num,
                    session_start_ts, session_end_ts, duration_seconds,
                    connection_type, engagement_segment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, session_num, session_start_ts,
                 session_end_ts, duration_seconds, connection_type,
                 engagement_segment),
            )

    def load_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """Đọc sessions, có thể lọc theo user_id. Trả về list[dict]."""
        with self.conn:
            if user_id is None:
                cur = self.conn.execute(
                    "SELECT * FROM sessions ORDER BY session_start_ts"
                )
            else:
                cur = self.conn.execute(
                    "SELECT * FROM sessions WHERE user_id = ? "
                    "ORDER BY session_start_ts",
                    (user_id,),
                )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Progression
    # ------------------------------------------------------------------
    def upsert_progression(
        self,
        user_id: str,
        current_level: int,
        current_world: int,
        current_attempt_num: int,
    ) -> None:
        """Upsert progression_state (1 row / player)."""
        updated_at = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO progression_state (
                    user_id, current_level, current_world,
                    current_attempt_num, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, current_level, current_world,
                 current_attempt_num, updated_at),
            )

    def load_progression(self, user_id: str) -> dict[str, Any] | None:
        """Đọc progression của 1 player. Trả None nếu chưa có."""
        with self.conn:
            cur = self.conn.execute(
                "SELECT * FROM progression_state WHERE user_id = ?",
                (user_id,),
            )
        row = cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Economy
    # ------------------------------------------------------------------
    def upsert_economy(self, player: Player) -> None:
        """Upsert player_economy từ Player model."""
        player.touch()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_economy (
                    user_id, coins, gems, energy, lives, stars, tickets,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (player.user_id, player.coins, player.gems,
                 player.energy, player.lives, player.stars, player.tickets,
                 player.updated_at.isoformat()),
            )

    # ------------------------------------------------------------------
    # Bulk persistence (cho Bước 25 performance)
    # ------------------------------------------------------------------
    def bulk_persist_sessions(
        self,
        sessions: list[tuple[Player, "Session"]],
    ) -> None:
        """Persist N sessions + player snapshots trong 1 transaction.

        Mỗi session yêu cầu:
            - update players  (counter, economy, churn)
            - upsert player_economy
            - upsert player_progression
            - insert sessions

        Thay vì mở/close connection N lần (per-session), ta batch toàn bộ
        thành 1 transaction duy nhất. Với WAL journal_mode đã bật, đây là
        điểm tăng tốc lớn nhất của Bước 25 (~50-100x cho 5000 players).

        Args:
            sessions: list các (Player, Session). Player đã được cập nhật
                bởi simulation engine trước khi flush.
        """
        if not sessions:
            return

        with self.transaction() as conn:
            player_rows: list[dict[str, Any]] = []
            economy_rows: list[tuple] = []
            progression_rows: list[tuple] = []
            session_rows: list[tuple] = []

            for player, session in sessions:
                player.touch()
                # 1. players table
                player_rows.append(_player_to_row(player))
                # 2. player_economy
                economy_rows.append((
                    player.user_id, player.coins, player.gems,
                    player.energy, player.lives, player.stars, player.tickets,
                    player.updated_at.isoformat(),
                ))
                # 3. player_progression
                progression_rows.append((
                    player.user_id, player.current_level,
                    player.current_world, player.current_attempt_num,
                    player.updated_at.isoformat(),
                ))
                # 4. sessions table
                session_rows.append((
                    session.session_id, player.user_id, session.session_num,
                    session.session_start_ts, session.session_end_ts,
                    session.duration_seconds, session.connection_type,
                    player.engagement_segment,
                ))

            # executemany: 1 prepared statement chạy N lần
            conn.executemany(
                """
                UPDATE players SET
                    current_level       = :current_level,
                    current_world       = :current_world,
                    current_attempt_num = :current_attempt_num,
                    coins               = :coins,
                    gems                = :gems,
                    energy              = :energy,
                    lives               = :lives,
                    stars               = :stars,
                    tickets             = :tickets,
                    session_count       = :session_count,
                    transaction_num     = :transaction_num,
                    lifetime_revenue    = :lifetime_revenue,
                    last_session_date   = :last_session_date,
                    churn_status        = :churn_status,
                    is_new_install      = :is_new_install,
                    updated_at          = :updated_at
                WHERE user_id = :user_id
                """,
                player_rows,
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO player_economy (
                    user_id, coins, gems, energy, lives, stars, tickets,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                economy_rows,
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO progression_state (
                    user_id, current_level, current_world,
                    current_attempt_num, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                progression_rows,
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO sessions (
                    session_id, user_id, session_num,
                    session_start_ts, session_end_ts, duration_seconds,
                    connection_type, engagement_segment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                session_rows,
            )

    # ------------------------------------------------------------------
    # Queue (cho Bước 16)
    # ------------------------------------------------------------------
    def enqueue_event(self, payload: dict[str, Any]) -> int:
        """Thêm 1 event vào queue, trả về id."""
        created_at = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO queue (payload, created_at, status) "
                "VALUES (?, ?, 'pending')",
                (json.dumps(payload, ensure_ascii=False), created_at),
            )
        return cur.lastrowid

    def dequeue_batch(self, limit: int = 100,
                      status: str = "pending") -> list[dict[str, Any]]:
        """Lấy ra tối đa ``limit`` event theo status."""
        with self.conn:
            cur = self.conn.execute(
                "SELECT id, payload FROM queue WHERE status = ? "
                "ORDER BY id LIMIT ?",
                (status, limit),
            )
        return [{"id": r["id"], "payload": json.loads(r["payload"])}
                for r in cur.fetchall()]

    def mark_event_sent(self, event_id: int) -> None:
        """Đánh dấu event là đã gửi."""
        sent_at = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE queue SET status = 'sent', sent_at = ? WHERE id = ?",
                (sent_at, event_id),
            )

    def mark_event_failed(
        self,
        event_id: int,
        error: str,
        next_retry_at: str | None = None,
    ) -> None:
        """Đánh dấu event retry fail + tăng retry_count.

        Dùng trong Bước 21 retry mechanism.

        Args:
            event_id: event id trong queue.
            error: error message.
            next_retry_at: ISO timestamp retry tiếp theo. None = không retry.
        """
        attempt_at = datetime.utcnow().isoformat()

        def _do(conn):
            conn.execute(
                """
                UPDATE queue
                SET retry_count     = retry_count + 1,
                    last_error       = ?,
                    next_retry_at   = ?,
                    last_attempt_at = ?
                WHERE id = ?
                """,
                (error, next_retry_at, attempt_at, event_id),
            )

        # Tự mở connection nếu chưa mở
        if self._conn is None:
            with self as s:
                _do(s.conn)
        else:
            _do(self.conn)
            self.conn.commit()

    def mark_retry_exhausted(self, event_id: int, error: str) -> None:
        """Đánh dấu event là permanently failed.

        Args:
            event_id: event id trong queue.
            error: error message cuối cùng.
        """
        attempt_at = datetime.utcnow().isoformat()

        def _do(conn):
            conn.execute(
                """
                UPDATE queue
                SET status           = 'failed',
                    last_error       = ?,
                    last_attempt_at = ?
                WHERE id = ?
                """,
                (error, attempt_at, event_id),
            )

        if self._conn is None:
            with self as s:
                _do(s.conn)
        else:
            _do(self.conn)
            self.conn.commit()


# =========================================================================
# Helpers – convert giữa Player dataclass và sqlite row
# =========================================================================

def _player_to_row(p: Player) -> dict[str, Any]:
    """Chuyển Player thành dict tương thích với SQL binding.

    Lưu ý:
        - datetime -> ISO 8601 string
        - bool -> 0/1
        - campaign_id = None -> NULL
    """
    return {
        "user_id": p.user_id,
        "country": p.country,
        "platform": p.platform,
        "device": p.device,
        "manufacturer": p.manufacturer,
        "os_version": p.os_version,
        "install_date": p.install_date.isoformat(),
        "acquisition_source": p.acquisition_source,
        "campaign_id": p.campaign_id,
        "player_segment": p.player_segment,
        "engagement_segment": p.engagement_segment,
        "churn_segment": p.churn_segment,
        "current_level": p.current_level,
        "current_world": p.current_world,
        "current_attempt_num": p.current_attempt_num,
        "coins": p.coins,
        "gems": p.gems,
        "energy": p.energy,
        "lives": p.lives,
        "stars": p.stars,
        "tickets": p.tickets,
        "session_count": p.session_count,
        "transaction_num": p.transaction_num,
        "lifetime_revenue": p.lifetime_revenue,
        "last_session_date": (p.last_session_date.isoformat()
                              if p.last_session_date else None),
        "churn_status": p.churn_status,
        "is_new_install": 1 if p.is_new_install else 0,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _row_to_player(row: sqlite3.Row | dict[str, Any]) -> Player:
    """Chuyển sqlite Row thành Player (ngược lại của _player_to_row)."""
    data = dict(row)

    def _parse_dt(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        return datetime.fromisoformat(value)

    return Player(
        user_id=data["user_id"],
        country=data["country"],
        platform=data["platform"],
        device=data["device"],
        manufacturer=data["manufacturer"],
        os_version=data["os_version"],
        install_date=datetime.fromisoformat(data["install_date"]),
        acquisition_source=data["acquisition_source"],
        campaign_id=data["campaign_id"],
        player_segment=data["player_segment"],
        engagement_segment=data["engagement_segment"],
        churn_segment=data["churn_segment"],
        current_level=data["current_level"],
        current_world=data["current_world"],
        current_attempt_num=data["current_attempt_num"],
        coins=data["coins"],
        gems=data["gems"],
        energy=data["energy"],
        lives=data["lives"],
        stars=data["stars"],
        tickets=data["tickets"],
        session_count=data["session_count"],
        transaction_num=data["transaction_num"],
        lifetime_revenue=data["lifetime_revenue"],
        last_session_date=_parse_dt(data.get("last_session_date")),
        churn_status=data["churn_status"],
        is_new_install=bool(data["is_new_install"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )
