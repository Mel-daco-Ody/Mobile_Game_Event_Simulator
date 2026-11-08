"""Main entrypoint – chạy large-scale Mobile Game Event Simulator.

Theo BƯỚC 25 INSTRUCTION.md:

    START
      ↓
    Load configuration
      ↓
    Load GameAnalytics credentials (.env)
      ↓
    Initialize database (SQLite)
      ↓
    Create / load players
      ↓
    For each simulation day:
      │
      ├── Determine active players (retention model)
      │
      ├── Create sessions
      ├── Create user event (session start)
      ├── Generate gameplay (progression + design)
      ├── Generate ads (impression + rewarded)
      ├── Generate resources (source/sink)
      ├── Generate IAP (business, theo payer segment)
      ├── Generate rare errors
      ├── Generate session_end
      │
      ├── Validate
      ├── Write raw
      ├── Build batches (< 1 MB gzip)
      └── POST GameAnalytics
      ↓
    Next simulation day
      ↓
    FINISH

Nhớ:

    - Dùng stream/batch chứ không load toàn bộ events vào RAM.
    - Mỗi batch xong → ghi raw + gửi API → clear buffer → lặp tiếp.

Usage::

    python main.py --players 1000 --days 7 --seed 42 --dry-run
    python main.py --players 100000 --days 30 --seed 42
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# UTF-8 cho stdout
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import credentials as credentials_mod  # noqa: E402
from config.credentials import (  # noqa: E402
    GameAnalyticsCredentials,
    find_env_file,
    get_credentials,
)
from generators import generate_players  # noqa: E402
from gameanalytics.client import GameAnalyticsClient  # noqa: E402
from models import Player  # noqa: E402
from simulation.daily_runner import (  # noqa: E402
    DailyRunner,
    SimulationRunner,
    create_batch_sink,
)
from simulation.report import ReportBuilder, SimulationReport  # noqa: E402
from storage.raw_writer import (  # noqa: E402
    DEFAULT_RAW_ROOT,
    RawDataWriter,
)
from storage.sqlite import SQLiteStorage  # noqa: E402


# =========================================================================
# Logging setup
# =========================================================================

def setup_logging(level: str = "INFO") -> None:
    """Cấu hình logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Giảm noise từ urllib3 / requests
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# =========================================================================
# CLI Args
# =========================================================================

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Mobile Game Event Simulator – large-scale pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Scale
    scale = parser.add_argument_group("Scale")
    scale.add_argument(
        "--players", type=int, default=1000,
        help="Số player cần generate.",
    )
    scale.add_argument(
        "--days", type=int, default=7,
        help="Số ngày simulation.",
    )
    scale.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (deterministic nếu cố định).",
    )

    # Storage
    storage = parser.add_argument_group("Storage")
    storage.add_argument(
        "--db", type=str, default="simulator.db",
        help="Đường dẫn SQLite DB.",
    )
    storage.add_argument(
        "--raw-root", type=str, default=str(DEFAULT_RAW_ROOT),
        help="Root folder cho raw data lake.",
    )

    # GameAnalytics
    ga = parser.add_argument_group("GameAnalytics")
    ga.add_argument(
        "--ga-env", type=str, default="sandbox",
        choices=("sandbox", "production"),
        help="Môi trường GameAnalytics.",
    )
    ga.add_argument(
        "--env-file", type=str, default=None,
        help="Path tới .env file (None = auto-detect).",
    )
    ga.add_argument(
        "--dry-run", action="store_true",
        help="Skip POST API (chỉ ghi raw + DB).",
    )

    # Reporting
    report = parser.add_argument_group("Reporting")
    report.add_argument(
        "--report-output", type=str, default="data/report.json",
        help="File output cho simulation report JSON.",
    )

    # Misc
    misc = parser.add_argument_group("Misc")
    misc.add_argument(
        "--start-date", type=str, default=None,
        help="Ngày bắt đầu (YYYY-MM-DD). Default: today UTC.",
    )
    misc.add_argument(
        "--log-level", type=str, default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Log level.",
    )
    misc.add_argument(
        "--init-db", action="store_true",
        help="Khởi tạo lại DB từ đầu (xóa tables).",
    )

    return parser.parse_args()


# =========================================================================
# Main pipeline
# =========================================================================

def main() -> int:
    """Main entrypoint."""
    args = parse_args()
    setup_logging(args.log_level)

    logger.info("=" * 70)
    logger.info("MOBILE GAME EVENT SIMULATOR – BƯỚC 25")
    logger.info("=" * 70)

    # ----------------------------------------------------------------------
    # 1. Load configuration
    # ----------------------------------------------------------------------
    logger.info("[STEP 1] Load configuration")

    # ----------------------------------------------------------------------
    # 2. Load credentials
    # ----------------------------------------------------------------------
    logger.info("[STEP 2] Load GameAnalytics credentials (.env)")
    creds = _load_credentials(args)
    if creds is None and not args.dry_run:
        logger.warning(
            "Không tìm thấy credentials. Chuyển sang dry-run mode. "
            "Set GAME_KEY và SECRET_KEY trong .env để gửi API thật.",
        )
        args.dry_run = True
    elif creds is not None:
        logger.info(
            "Loaded credentials: game_key=%s, env=%s",
            creds.game_key[:8] + "..." if creds.game_key else "?",
            creds.environment,
        )

    # ----------------------------------------------------------------------
    # 3. Initialize database
    # ----------------------------------------------------------------------
    logger.info("[STEP 3] Initialize SQLite database: %s", args.db)
    storage = SQLiteStorage(db_path=args.db)
    with storage:
        storage.init_schema()
        if args.init_db:
            # Reset players table nếu cần
            try:
                storage.drop_all()
                storage.init_schema()
                logger.info("Re-initialized DB (init-db mode)")
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not drop tables: %s", e)

    # ----------------------------------------------------------------------
    # 4. Create / load players
    # ----------------------------------------------------------------------
    logger.info(
        "[STEP 4] Generate players (count=%d, seed=%d)",
        args.players, args.seed,
    )
    players = generate_players(count=args.players, seed=args.seed)
    logger.info(
        "Generated %d players (segments=%s)",
        len(players),
        _summarize_players(players),
    )

    # 4b. Snapshot players xuống SQLite (initial state).
    # Nếu DB đã có players từ lần chạy trước → save_players sẽ UPSERT
    # bằng INSERT OR REPLACE, an toàn và idempotent.
    with storage:
        storage.save_players(players)
    logger.info("Persisted %d players to SQLite (initial state)", len(players))

    # ----------------------------------------------------------------------
    # 5. Build pipeline components
    # ----------------------------------------------------------------------
    logger.info("[STEP 5] Build pipeline (Batcher + Validator + RawWriter + Client)")

    # Raw writer
    raw_writer = RawDataWriter(root=Path(args.raw_root))
    logger.info("RawDataWriter root: %s", raw_writer.root)

    # Client (chỉ tạo nếu không dry-run và có credentials)
    client = None
    if not args.dry_run and creds is not None:
        try:
            # GameAnalyticsCredentials là dataclass (game_key, secret_key,
            # environment) – KHÔNG có method create_client().
            # Dùng GameAnalyticsClient constructor trực tiếp.
            client = GameAnalyticsClient(
                game_key=creds.game_key,
                secret_key=creds.secret_key,
                environment=creds.environment,
            )
            base_url = (
                "sandbox" if creds.is_sandbox else "production"
            )
            logger.info(
                "GameAnalytics client created (env=%s)",
                base_url,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not create client: %s. Falling back to dry-run.", e)
            args.dry_run = True

    # Batch sink
    sink = create_batch_sink(
        raw_writer=raw_writer,
        client=client,
        dry_run=args.dry_run,
    )

    # DailyRunner + SimulationRunner
    daily_runner = DailyRunner(rng=random.Random(args.seed), sink=sink)
    sim_runner = SimulationRunner(
        daily_runner=daily_runner,
        storage=storage,
        raw_writer=raw_writer,
        client=client,
    )

    # ----------------------------------------------------------------------
    # 6. Run simulation (for each day)
    # ----------------------------------------------------------------------
    start_date = _parse_start_date(args.start_date)
    logger.info(
        "[STEP 6] Run simulation: %d days from %s",
        args.days, start_date.strftime("%Y-%m-%d"),
    )

    # Build install_date cho từng player (mỗi player install 1 ngày trong window)
    install_dates = _spread_install_dates(
        players, start_date, max_days_back=min(args.days, 7),
    )

    # Build new_install_user_ids map (cho DailyRunner force_active)
    new_install_user_ids: dict[int, set[str]] = {}
    for i, player in enumerate(players):
        if player.install_date >= start_date:
            # Mới install trong window simulation
            day_offset = (player.install_date - start_date).days
            if day_offset not in new_install_user_ids:
                new_install_user_ids[day_offset] = set()
            new_install_user_ids[day_offset].add(player.user_id)

    report_builder = ReportBuilder()

    # ------------------------------------------------------------------
    # SINGLE SOURCE OF TRUTH:
    # SimulationRunner.run() là entry point chính — nó điều phối
    # DailyRunner, aggregate totals (revenue, paying_players, ...),
    # wire persistence_callback, và gọi on_day_complete callback để
    # ReportBuilder thu thập by_day breakdown.
    # ------------------------------------------------------------------
    def _on_day_complete(current_date: datetime, daily_stats: dict[str, Any]) -> None:
        """Callback: log progress + feed ReportBuilder."""
        logger.info(
            "Day %s: sessions=%d, events=%d, batches=%d, errors=%d, "
            "wall=%ss events/s=%d, revenue_minor=%d",
            current_date.strftime("%Y-%m-%d"),
            daily_stats["sessions_active"],
            daily_stats["events_generated"],
            daily_stats["batches_flushed"],
            daily_stats["errors"],
            f"{daily_stats['wall_clock_sec']:.2f}",
            int(daily_stats["events_generated"]
                / max(0.001, daily_stats["wall_clock_sec"])),
            daily_stats.get("revenue_minor", 0),
        )
        report_builder.add_day(current_date, daily_stats)

    aggregate_stats = sim_runner.run(
        players=players,
        start_date=start_date,
        n_days=args.days,
        new_install_user_ids=new_install_user_ids,
        on_day_complete=_on_day_complete,
    )

    end_date = start_date + timedelta(days=args.days - 1)
    # Pass aggregate_stats từ SimulationRunner (single source of truth)
    report = report_builder.build(
        start_date=start_date,
        end_date=end_date,
        total_players=len(players),
        aggregate_stats=aggregate_stats,
    )

    # ----------------------------------------------------------------------
    # 7. Save final report
    # ----------------------------------------------------------------------
    logger.info("[STEP 7] Save report: %s", args.report_output)
    report_path = report.save_json(args.report_output)
    logger.info("Report saved to: %s", report_path)

    # ----------------------------------------------------------------------
    # 8. Final summary
    # ----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("KẾT QUẢ SIMULATION")
    print("=" * 70)
    print(f"  Days              : {report.n_days}")
    print(f"  Players           : {report.total_players:,}")
    print(f"  Sessions          : {report.total_sessions:,}")
    print(f"  Events            : {report.total_events:,}")
    print(f"  Batches           : {report.total_batches:,}")
    print(f"  Errors            : {sum(d.errors for d in report.by_day)}")
    print(f"  Wall clock        : {report.wall_clock_sec:.2f}s")
    print(f"  Throughput        : {report.events_per_second:.0f} events/sec")
    print(f"  Avg events/session: {report.avg_events_per_session:.1f}")
    print(f"  Avg DAU           : {report.avg_dau:.0f}")
    print(f"  Revenue (minor)   : {report.total_revenue_minor:,}")
    print(f"  Revenue (USD)     : ${report.total_revenue_usd:,.2f}")
    print(f"  Paying players    : {report.total_paying_players:,}")
    print(f"  ARPU              : ${report.arpu:.4f}")
    arppu_val = report.arppu
    print(f"  ARPPU             : {f'${arppu_val:.4f}' if arppu_val is not None else 'N/A'}")
    print(f"  By category       : {dict(report.by_category)}")
    print(f"  By country        : {dict(report.by_country)}")
    print()
    print(f"  Report JSON       : {report_path}")
    print(f"  Raw data          : {raw_writer.root}")
    print(f"  SQLite            : {Path(args.db).resolve()}")
    print(f"  Mode              : {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print("=" * 70)

    return 0


# =========================================================================
# Helpers
# =========================================================================

def _load_credentials(args: argparse.Namespace) -> GameAnalyticsCredentials | None:
    """Load credentials từ .env file."""
    try:
        if args.env_file:
            # User-specified path → dùng trực tiếp
            env_path = Path(args.env_file)
        else:
            # Auto-detect: ưu tiên tìm ở _PROJECT_ROOT/.env trước
            # (find_env_file() mặc định tìm ở cwd, nhưng khi chạy từ
            # _PROJECT_ROOT thì '.env' = 'prj/.env' relative sai)
            candidate = _PROJECT_ROOT / ".env"
            env_path = candidate if candidate.is_file() else None
            if env_path is None:
                env_path = find_env_file()
        # get_credentials() chỉ nhận `env_path` (không có `env_file`/`env`).
        # `env=args.ga_env` được xử lý bởi GameAnalyticsClient sau.
        creds = get_credentials(env_path=env_path)
        # creds đã được validate (game_key & secret_key không rỗng) bên trong
        # GameAnalyticsCredentials.__post_init__; chỉ cần check có key.
        return creds if (creds.game_key and creds.secret_key) else None
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not load credentials: %s", e)
        return None


def _parse_start_date(s: str | None) -> datetime:
    """Parse start_date từ string → datetime UTC (00:00:00)."""
    if s is None:
        return datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _summarize_players(players: list[Player]) -> dict[str, int]:
    """Tóm tắt distribution segments."""
    from collections import Counter
    segments = Counter(p.player_segment for p in players)
    return dict(segments)


def _spread_install_dates(
    players: list[Player],
    start_date: datetime,
    max_days_back: int = 7,
) -> dict[str, datetime]:
    """Spread install_date cho players.

    Args:
        players: list Player.
        start_date: ngày bắt đầu simulation.
        max_days_back: max số ngày back từ start_date.

    Returns:
        dict {user_id: install_date}.
    """
    import random
    rng = random.Random(42)  # Deterministic install dates
    for i, p in enumerate(players):
        days_back = rng.randint(0, max_days_back)
        p.install_date = (
            start_date - timedelta(days=days_back)
        ).replace(hour=12, minute=0, second=0, microsecond=0)
        p.is_new_install = (days_back == 0)
    return {p.user_id: p.install_date for p in players}


# =========================================================================
# Entry point
# =========================================================================

if __name__ == "__main__":
    sys.exit(main())
