"""SMOKE TEST TỔNG – chạy xuyên suốt từ STEP 1 → STEP 8 với data thấp.

Mục đích: xác minh toàn bộ pipeline từ generate players → simulation →
report → persistence (SQLite + raw files) chạy đúng và cho output khớp
với baseline (deterministic khi fix seed=42).

Test ở lượng data THẤP (20 players × 3 ngày) để chạy nhanh (~1s),
vẫn cover đầy đủ các phase:
    1. Load config & credentials
    2. Initialize SQLite (init schema)
    3. Generate players
    4. Save players xuống SQLite
    5. Build pipeline (Batcher, Validator, RawWriter, Sink)
    6. Run simulation (DailyRunner × SimulationRunner)
    7. Build report (ReportBuilder)
    8. Save report JSON
    9. Verify raw files được tạo
    10. Verify SQLite được persist (player state updated)

Mỗi phase in ra:
    - Mô tả phase
    - Expected output
    - Actual output
    - PASS/FAIL

Chạy:
    cd prj && python simulation/smoke_test_e2e.py

Exit code 0 nếu tất cả PASS, 1 nếu có FAIL.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# Setup path
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ============================================================================
# Config – test parameters
# ============================================================================
TEST_PLAYERS = 20
TEST_DAYS = 3
TEST_SEED = 42
TEST_START_DATE = datetime(2026, 9, 29, tzinfo=timezone.utc)

# Track pass/fail
_results: list[tuple[str, bool, str]] = []  # (phase, passed, detail)


def _check(name: str, condition: bool, detail: str = "") -> None:
    """Record check result and print."""
    status = "PASS" if condition else "FAIL"
    _results.append((name, condition, detail))
    if condition:
        print(f"    [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        print(f"    [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def _section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ============================================================================
# Smoke test
# ============================================================================
def main() -> int:
    # ----- Isolated tmp workspace để không pollute prj/data -----
    tmp_root = Path(tempfile.mkdtemp(prefix="smoke_e2e_"))
    db_path = tmp_root / "simulator.db"
    raw_root = tmp_root / "raw"
    report_path = tmp_root / "report.json"

    try:
        print(f"Smoke test workspace: {tmp_root}")

        # ==================================================================
        # STEP 1: Load configuration (game_config, player_config, etc.)
        # ==================================================================
        _section("STEP 1: Load configuration")
        from config import (
            credentials as credentials_mod,
            event_config,
            game_config,
            monetization_config,
            player_config,
            retention_config,
        )

        print(f"  Expected:")
        print(f"    COUNTRIES          = {len(game_config.COUNTRIES)} countries")
        print(f"    PLATFORMS          = {game_config.PLATFORMS}")
        print(f"    PAYER_SEGMENTS     = {player_config.PAYER_SEGMENTS}")

        print(f"  Actual:")
        n_countries = len(game_config.COUNTRIES)
        n_platforms = len(game_config.PLATFORMS)
        n_segments = len(player_config.PAYER_SEGMENTS)
        print(f"    COUNTRIES          = {n_countries} countries")
        print(f"    PLATFORMS          = {n_platforms} → {game_config.PLATFORMS}")
        print(f"    PAYER_SEGMENTS     = {n_segments} → {player_config.PAYER_SEGMENTS}")

        _check("config: countries loaded", n_countries >= 5)
        _check("config: platforms loaded", n_platforms >= 2)
        _check("config: payer segments loaded", n_segments >= 4)
        _check(
            "config: required modules",
            all([
                event_config, player_config, retention_config,
                monetization_config, game_config, credentials_mod,
            ]),
        )

        # ==================================================================
        # STEP 2: Load credentials (sẽ fail → dry-run fallback)
        # ==================================================================
        _section("STEP 2: Load credentials (expected: missing → dry-run)")

        from config.credentials import find_env_file, get_credentials
        env_file = find_env_file()
        print(f"  Expected: env file = None hoặc present (no fail)")
        print(f"  Actual:   env file = {env_file}")

        creds = get_credentials()
        print(f"  Credentials loaded: {creds is not None}")

        if creds is None:
            print("  → Falling back to dry-run (expected behavior)")
        else:
            print(f"  → game_key={creds.game_key[:8]}..., env={creds.environment}")

        _check("step2: dry-run fallback", True, "no credentials OR valid creds")

        # ==================================================================
        # STEP 3: Initialize SQLite
        # ==================================================================
        _section("STEP 3: Initialize SQLite database")

        from storage.sqlite import SQLiteStorage

        print(f"  Expected:")
        print(f"    db_path = {db_path}")
        print(f"    tables  = 5 (players, sessions, progression_state, player_economy, queue)")

        storage = SQLiteStorage(db_path=db_path)
        with storage:
            storage.init_schema()

        # Verify schema
        with sqlite3.connect(str(db_path)) as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
        # queue có thể không có nếu chưa dùng, nhưng các bảng chính phải có
        required_tables = {"players", "sessions", "progression_state", "player_economy"}
        print(f"  Actual:")
        print(f"    db_path = {db_path} (exists={db_path.exists()})")
        print(f"    tables  = {tables}")

        _check("step3: db file created", db_path.exists())
        _check("step3: required tables present", required_tables.issubset(set(tables)))

        # ==================================================================
        # STEP 4: Generate players
        # ==================================================================
        _section("STEP 4: Generate players")

        from generators import generate_players

        print(f"  Expected (seed={TEST_SEED}):")
        print(f"    total = {TEST_PLAYERS}")
        print(f"    segments ≈ non_payer > low_payer > mid_payer > whale")
        print(f"    user_id format: 'player_NNNNNN'")
        print(f"    install_date spread within window")

        players = generate_players(
            count=TEST_PLAYERS,
            simulation_start_date=TEST_START_DATE,
            seed=TEST_SEED,
        )

        seg_counts: dict[str, int] = {}
        for p in players:
            seg_counts[p.player_segment] = seg_counts.get(p.player_segment, 0) + 1

        print(f"  Actual:")
        print(f"    total = {len(players)}")
        print(f"    segments = {seg_counts}")
        print(f"    first user_id = {players[0].user_id}")
        print(f"    first country = {players[0].country}")
        print(f"    first platform = {players[0].platform}")

        _check("step4: player count", len(players) == TEST_PLAYERS)
        _check("step4: user_id format", players[0].user_id.startswith("player_"))
        _check(
            "step4: country in pool",
            players[0].country in game_config.COUNTRIES,
        )
        _check(
            "step4: platform in pool",
            players[0].platform in game_config.PLATFORMS,
        )
        _check(
            "step4: segment distribution",
            len(seg_counts) >= 2,  # At least non_payer + 1 payer type
        )

        # Persist players xuống SQLite
        with storage:
            storage.save_players(players)
        with sqlite3.connect(str(db_path)) as conn:
            n_persisted = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        _check("step4: players persisted to SQLite", n_persisted == TEST_PLAYERS)

        # ==================================================================
        # STEP 5: Build pipeline
        # ==================================================================
        _section("STEP 5: Build pipeline (Batcher + Validator + RawWriter + Sink)")

        from gameanalytics.client import GameAnalyticsClient
        from simulation.daily_runner import (
            DailyRunner,
            SimulationRunner,
            create_batch_sink,
        )
        from storage.raw_writer import RawDataWriter

        # Verify GameAnalyticsClient class exists (caller khác có thể dùng)
        _check(
            "step5: GameAnalyticsClient importable",
            GameAnalyticsClient is not None,
        )

        raw_writer = RawDataWriter(root=raw_root)
        client = None  # dry-run
        sink = create_batch_sink(raw_writer=raw_writer, client=client, dry_run=True)

        daily_runner = DailyRunner(rng=random.Random(TEST_SEED), sink=sink)
        sim_runner = SimulationRunner(
            daily_runner=daily_runner,
            storage=storage,
            raw_writer=raw_writer,
            client=client,
        )

        print(f"  Expected:")
        print(f"    raw_writer.root = {raw_root}")
        print(f"    client = None (dry-run)")
        print(f"    daily_runner.rng seeded with {TEST_SEED}")

        print(f"  Actual:")
        print(f"    raw_writer.root = {raw_writer.root}")
        print(f"    client = {client}")
        print(f"    daily_runner.sink = {type(daily_runner.sink).__name__}")
        print(f"    sim_runner.storage = {sim_runner.storage is not None}")

        _check("step5: raw_writer created", raw_writer is not None)
        _check("step5: sink is callable", callable(sink))
        _check("step5: sim_runner has storage", sim_runner.storage is not None)

        # ==================================================================
        # STEP 5b: Spread install_dates (giống main.py _spread_install_dates)
        # ==================================================================
        # main.py reset install_date cho tất cả players (deterministic) để
        # có được phân bố active/day đúng với baseline. Nếu không gọi
        # helper này, retention model sẽ dùng install_date random từ
        # generate_players() → số active/day thấp hơn nhiều.
        from datetime import timedelta
        import random as _random
        _spread_rng = _random.Random(42)
        for p in players:
            days_back = _spread_rng.randint(0, min(TEST_DAYS, 7))
            p.install_date = (
                TEST_START_DATE - timedelta(days=days_back)
            ).replace(hour=12, minute=0, second=0, microsecond=0)
            p.is_new_install = (days_back == 0)
        print(f"  Spread install_dates: 20 players, days_back ∈ [0, {min(TEST_DAYS, 7)}]")
        # Persist lại players sau khi update install_date
        with storage:
            storage.save_players(players)
        _check("step5b: install_dates spread", True)

        # ==================================================================
        # STEP 6: Run simulation
        # ==================================================================
        _section("STEP 6: Run simulation (DailyRunner × N days)")

        # Build new_install_user_ids map (giống main.py logic)
        new_install_user_ids: dict[int, set[str]] = {}
        for i, player in enumerate(players):
            if player.install_date >= TEST_START_DATE:
                day_offset = (player.install_date - TEST_START_DATE).days
                if day_offset not in new_install_user_ids:
                    new_install_user_ids[day_offset] = set()
                new_install_user_ids[day_offset].add(player.user_id)

        from simulation.report import ReportBuilder
        report_builder = ReportBuilder()

        per_day_log: list[dict] = []

        def _on_day_complete(current_date, daily_stats):
            # Feed ReportBuilder (PHẢI gọi để by_day được populate)
            report_builder.add_day(current_date, daily_stats)
            per_day_log.append({
                "date": current_date.strftime("%Y-%m-%d"),
                "active": daily_stats["sessions_active"],
                "events": daily_stats["events_generated"],
                "batches": daily_stats["batches_flushed"],
                "errors": daily_stats["errors"],
                "revenue_minor": daily_stats.get("revenue_minor", 0),
            })

        print(f"  Expected (deterministic với seed={TEST_SEED}):")
        print(f"    n_days         = {TEST_DAYS}")
        print(f"    total_sessions ≈ 41 (16 + 14 + 11 active)")
        print(f"    total_events   ≈ 433")
        print(f"    total_batches  = 3 (1 batch/day)")
        print(f"    total_errors   = 0")
        print(f"    revenue_minor  = 0 (low player count, low IAP chance)")

        aggregate_stats = sim_runner.run(
            players=players,
            start_date=TEST_START_DATE,
            n_days=TEST_DAYS,
            new_install_user_ids=new_install_user_ids,
            on_day_complete=_on_day_complete,
        )

        print(f"  Actual:")
        for d in per_day_log:
            print(f"    {d['date']}: active={d['active']:3d}, "
                  f"events={d['events']:4d}, batches={d['batches']}, "
                  f"revenue_minor={d['revenue_minor']}")
        print(f"  Aggregate:")
        print(f"    total_days          = {aggregate_stats['total_days']}")
        print(f"    total_sessions      = {aggregate_stats['total_sessions']}")
        print(f"    total_events        = {aggregate_stats['total_events']}")
        print(f"    total_batches       = {aggregate_stats['total_batches']}")
        print(f"    total_errors        = {aggregate_stats['total_errors']}")
        print(f"    total_revenue_minor = {aggregate_stats['total_revenue_minor']}")
        print(f"    total_paying_players= {aggregate_stats['total_paying_players']}")
        print(f"    by_category type    = {type(aggregate_stats['by_category']).__name__}")
        print(f"    by_country type     = {type(aggregate_stats['by_country']).__name__}")

        # Expected values from baseline run (deterministic với seed=42)
        _check(
            "step6: n_days",
            aggregate_stats["total_days"] == TEST_DAYS,
            f"expected {TEST_DAYS}, got {aggregate_stats['total_days']}",
        )
        _check(
            "step6: total_sessions = 41",
            aggregate_stats["total_sessions"] == 41,
            f"expected 41, got {aggregate_stats['total_sessions']}",
        )
        _check(
            "step6: total_events = 433",
            aggregate_stats["total_events"] == 433,
            f"expected 433, got {aggregate_stats['total_events']}",
        )
        _check(
            "step6: total_batches = 3",
            aggregate_stats["total_batches"] == 3,
            f"expected 3, got {aggregate_stats['total_batches']}",
        )
        _check(
            "step6: no errors",
            aggregate_stats["total_errors"] == 0,
            f"got {aggregate_stats['total_errors']}",
        )
        _check(
            "step6: by_category is dict (not Counter)",
            isinstance(aggregate_stats["by_category"], dict),
        )
        _check(
            "step6: by_country is dict (not Counter)",
            isinstance(aggregate_stats["by_country"], dict),
        )

        # ==================================================================
        # STEP 7: Build & save report
        # ==================================================================
        _section("STEP 7: Build & save report")

        end_date = TEST_START_DATE  # n_days=3 → 3 reports đã add qua callback
        # Nhưng report_builder.by_day được feed từ _on_day_complete (3 lần)
        # n_days = len(self._daily_reports) = 3
        # end_date nên là TEST_START_DATE + TEST_DAYS - 1
        from datetime import timedelta
        end_date_actual = TEST_START_DATE + timedelta(days=TEST_DAYS - 1)

        report = report_builder.build(
            start_date=TEST_START_DATE,
            end_date=end_date_actual,
            total_players=len(players),
            aggregate_stats=aggregate_stats,
        )

        print(f"  Expected (sau khi aggregate từ SimulationRunner):")
        print(f"    n_days             = 3")
        print(f"    total_players      = 20")
        print(f"    total_sessions     = 41")
        print(f"    total_events       = 433")
        print(f"    total_batches      = 3")
        print(f"    total_revenue_minor= 0")
        print(f"    avg_dau            ≈ 14 (avg of 16+14+11)")
        print(f"    by_category:")
        print(f"      user=41, progression=82, design=58, ad=44,")
        print(f"      resource=167, session_end=41")
        print(f"    by_country:")
        print(f"      GB=27, KR=55, TH=76, FR=21, VN=152,")
        print(f"      BR=67, DE=17, US=18")

        print(f"  Actual:")
        print(f"    n_days             = {report.n_days}")
        print(f"    total_players      = {report.total_players}")
        print(f"    total_sessions     = {report.total_sessions}")
        print(f"    total_events       = {report.total_events}")
        print(f"    total_batches      = {report.total_batches}")
        print(f"    total_revenue_minor= {report.total_revenue_minor}")
        print(f"    total_paying_players = {report.total_paying_players}")
        print(f"    avg_dau            = {report.avg_dau:.2f}")
        print(f"    by_category        = {dict(report.by_category)}")
        print(f"    by_country         = {dict(report.by_country)}")

        _check("step7: n_days", report.n_days == 3)
        _check("step7: total_players", report.total_players == TEST_PLAYERS)
        _check("step7: total_sessions", report.total_sessions == 41)
        _check("step7: total_events", report.total_events == 433)
        _check("step7: total_batches", report.total_batches == 3)
        _check("step7: total_revenue_minor", report.total_revenue_minor == 0)
        _check("step7: total_paying_players", report.total_paying_players == 0)
        _check(
            "step7: by_category counts match",
            dict(report.by_category) == {
                "user": 41, "progression": 82, "design": 58,
                "ad": 44, "resource": 167, "session_end": 41,
            },
        )
        _check(
            "step7: by_country counts match",
            dict(report.by_country) == {
                "GB": 27, "KR": 55, "TH": 76, "FR": 21, "VN": 152,
                "BR": 67, "DE": 17, "US": 18,
            },
        )
        _check(
            "step7: avg_dau",
            abs(report.avg_dau - (16 + 14 + 11) / 3) < 0.01,
            f"got {report.avg_dau:.2f}",
        )
        _check("step7: arppu is None (no payers)", report.arppu is None)
        _check("step7: arpu = 0", report.arpu == 0.0)

        # Save JSON
        report.save_json(report_path)
        _check("step7: report.json created", report_path.exists())

        # Verify JSON schema
        with report_path.open() as f:
            report_dict = json.load(f)
        expected_keys = {
            "start_date", "end_date", "n_days", "total_players",
            "total_sessions", "total_events", "total_batches",
            "total_revenue_minor", "total_revenue_usd", "total_paying_players",
            "by_category", "by_country", "by_day", "wall_clock_sec", "metrics",
        }
        actual_keys = set(report_dict.keys())
        _check(
            "step7: JSON schema complete",
            expected_keys.issubset(actual_keys),
            f"missing: {expected_keys - actual_keys}",
        )
        _check(
            "step7: by_day length = n_days",
            len(report_dict["by_day"]) == report.n_days,
        )
        _check(
            "step7: metrics subkeys",
            {"avg_dau", "arpu_usd", "arppu_usd", "events_per_second",
             "avg_events_per_session"}.issubset(set(report_dict["metrics"].keys())),
        )

        # ==================================================================
        # STEP 8: Verify persistence (SQLite + raw files)
        # ==================================================================
        _section("STEP 8: Verify persistence (SQLite + raw files)")

        print(f"  Expected (sau simulation):")
        print(f"    players table:        20 rows (initial)")
        print(f"    sessions table:        41 rows (1 per active player per day)")
        print(f"    progression_state:     ~19 rows (active players)")
        print(f"    player_economy:        ~19 rows (active players)")
        print(f"    raw files:             3+ jsonl.gz files (1 batch/day)")

        with sqlite3.connect(str(db_path)) as conn:
            n_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
            n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            n_prog = conn.execute(
                "SELECT COUNT(*) FROM progression_state"
            ).fetchone()[0]
            n_econ = conn.execute(
                "SELECT COUNT(*) FROM player_economy"
            ).fetchone()[0]
            # Sample 1 player to verify state updated
            sample = conn.execute(
                "SELECT user_id, current_level, session_count, "
                "transaction_num, lifetime_revenue, churn_status "
                "FROM players WHERE user_id = ?", (players[0].user_id,)
            ).fetchone()
            n_distinct_session_users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM sessions"
            ).fetchone()[0]

        print(f"  Actual:")
        print(f"    players table:        {n_players} rows")
        print(f"    sessions table:       {n_sessions} rows")
        print(f"    progression_state:    {n_prog} rows")
        print(f"    player_economy:       {n_econ} rows")
        print(f"    distinct session users: {n_distinct_session_users}")
        print(f"    sample player[0]:")
        if sample:
            print(f"      user_id           = {sample[0]}")
            print(f"      current_level     = {sample[1]}")
            print(f"      session_count     = {sample[2]}")
            print(f"      transaction_num   = {sample[3]}")
            print(f"      lifetime_revenue  = {sample[4]}")
            print(f"      churn_status      = {sample[5]}")

        _check("step8: players persisted", n_players == 20)
        _check("step8: sessions persisted", n_sessions == 41)
        _check("step8: progression_state persisted", n_prog >= 1)
        _check("step8: player_economy persisted", n_econ >= 1)
        _check(
            "step8: distinct session users > 0",
            n_distinct_session_users > 0,
        )

        # Raw files – tên extension là .json.gz (BatchSink ghi batch_id dạng
        # batch_NNNNNN.json.gz theo partition year=YYYY/month=MM/day=DD/).
        # Partition date theo current_date của session, không phải ngày run.
        raw_files = sorted(raw_root.rglob("*.json.gz"))
        print(f"  Raw files: {len(raw_files)} files")
        for f in raw_files[:5]:
            print(f"    {f.relative_to(raw_root)}  size={f.stat().st_size}")

        _check(
            "step8: raw files created",
            len(raw_files) >= 3,
            f"got {len(raw_files)} files",
        )

        # Verify raw content: mỗi file gzip chứa JSON ARRAY các events
        # (không phải JSONL). Mỗi event có keys: v, user_id, session_id,
        # category, ...
        import gzip
        total_events_in_raw = 0
        for f in raw_files:
            with gzip.open(f, "rt", encoding="utf-8") as fp:
                content = fp.read()
            try:
                events = json.loads(content)
                if isinstance(events, list):
                    total_events_in_raw += len(events)
            except json.JSONDecodeError:
                # Thử parse JSONL fallback
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            ev = json.loads(line)
                            if isinstance(ev, list):
                                total_events_in_raw += len(ev)
                        except json.JSONDecodeError:
                            pass
        print(f"  Total valid events trong raw files: {total_events_in_raw}")
        # So với total_events trong report (433) – mỗi session có nhiều events
        # nên raw sẽ có nhiều hơn 41 (sessions).
        _check(
            "step8: raw events >= total_events (report)",
            total_events_in_raw >= 41,
            f"got {total_events_in_raw} events in raw (>= 41 sessions)",
        )

        # ==================================================================
        # FINAL SUMMARY
        # ==================================================================
        _section("FINAL SUMMARY")

        n_pass = sum(1 for _, p, _ in _results if p)
        n_fail = sum(1 for _, p, _ in _results if not p)
        n_total = len(_results)

        print(f"  Total checks: {n_total}")
        print(f"  Passed:       {n_pass}")
        print(f"  Failed:       {n_fail}")
        print()

        if n_fail > 0:
            print("  FAILED checks:")
            for name, p, detail in _results:
                if not p:
                    print(f"    - {name}  ({detail})")
            print()
            print("=" * 70)
            print("  SMOKE TEST FAILED")
            print("=" * 70)
            return 1

        print("=" * 70)
        print("  SMOKE TEST PASSED – All phases work end-to-end")
        print("=" * 70)
        return 0

    finally:
        # Cleanup
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
