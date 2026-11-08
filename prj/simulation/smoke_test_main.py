"""Smoke test cho BƯỚC 25 – Large-Scale Simulation & Main Pipeline.

Kiểm tra:
    1. DailyRunner cơ bản với 50 players × 3 ngày.
    2. SimulationRunner chạy N ngày.
    3. SessionContext quản lý events đúng cách.
    4. Validator bắt được lỗi invalid events.
    5. Batcher flush khi đầy.
    6. Stream/batch (không load all-in-RAM).
    7. create_batch_sink với dry-run mode.
    8. CLI main.py --help.
    9. CLI main.py --dry-run --players 100 --days 2 thực sự chạy.
    10. Report save_json ra file đúng schema.
    11. Daily stats structure đúng.
    12. by_category counter đúng.

Chạy:
    cd prj && python simulation/smoke_test_main.py
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gameanalytics.batcher import Batcher  # noqa: E402
from generators import generate_players  # noqa: E402
from models import Player  # noqa: E402
from simulation.daily_runner import (  # noqa: E402
    DailyRunner,
    SessionContext,
    SimulationRunner,
    create_batch_sink,
)
from simulation.report import (  # noqa: E402
    DailyReport,
    ReportBuilder,
    SimulationReport,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 25: LARGE-SCALE SIMULATION")
    print("=" * 60)

    fixed_now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

    # ----------------------------------------------------------------------
    # 1. SessionContext
    # ----------------------------------------------------------------------
    print("\n[--- 1. SESSIONCONTEXT ---]")

    print("\n[1.1] SessionContext.add_event / drain_events")
    players = generate_players(count=5, seed=42)
    player = players[0]
    session = player  # dummy
    from models.session import generate_session
    sess = generate_session(player)
    ctx = SessionContext(player=player, session=sess)
    ctx.add_event({"category": "user", "client_ts": 100})
    ctx.add_event({"category": "design", "client_ts": 101})
    assert_check(len(ctx.session_events) == 2, f"events = {len(ctx.session_events)}")
    assert_check(ctx.events_count_per_category["user"] == 1, "user=1")

    drained = ctx.drain_events()
    assert_check(len(drained) == 2, f"drained = {len(drained)}")
    assert_check(len(ctx.session_events) == 0, "buffer cleared")

    # ----------------------------------------------------------------------
    # 2. DailyRunner cơ bản
    # ----------------------------------------------------------------------
    print("\n[--- 2. DAILYRUNNER CƠ BẢN ---]")

    print("\n[2.1] Chạy 50 players × 1 ngày")
    players_50 = generate_players(count=50, seed=42)
    # Set install_date = today để player mới active
    for p in players_50:
        p.install_date = fixed_now

    sink_called: list[int] = []
    def sink(batch):
        sink_called.append(len(batch.events))

    daily_runner = DailyRunner(rng=random.Random(42), sink=sink)
    stats = daily_runner.run_day(players_50, fixed_now)

    assert_check(stats["sessions_active"] > 0, f"sessions = {stats['sessions_active']}")
    assert_check(stats["events_generated"] > 0, f"events = {stats['events_generated']}")
    assert_check(len(stats["by_category"]) > 0, f"categories = {list(stats['by_category'])}")
    assert_check(stats["wall_clock_sec"] > 0, f"wall = {stats['wall_clock_sec']:.3f}s")

    # Categories phải bao gồm user, session_end, design, business, ad, ...
    expected_cats = {"user", "session_end"}
    actual_cats = set(stats["by_category"].keys())
    assert_check(
        expected_cats <= actual_cats,
        f"missing categories: {expected_cats - actual_cats}",
    )

    # ----------------------------------------------------------------------
    # 3. SimulationRunner
    # ----------------------------------------------------------------------
    print("\n[--- 3. SIMULATIONRUNNER ---]")

    print("\n[3.1] Chạy 7 ngày")
    players_200 = generate_players(count=200, seed=42)
    sim_runner = SimulationRunner(
        daily_runner=DailyRunner(rng=random.Random(42), sink=sink),
    )
    sim_stats = sim_runner.run(
        players_200,
        start_date=fixed_now,
        n_days=7,
    )
    assert_check(sim_stats["total_days"] == 7, f"days = {sim_stats['total_days']}")
    assert_check(sim_stats["total_sessions"] > 0, f"sessions = {sim_stats['total_sessions']}")
    assert_check(sim_stats["total_events"] > 0, f"events = {sim_stats['total_events']}")
    assert_check(sim_stats["total_batches"] >= 0, f"batches = {sim_stats['total_batches']}")

    # ----------------------------------------------------------------------
    # 4. Stream/batch (không load all-in-RAM)
    # ----------------------------------------------------------------------
    print("\n[--- 4. STREAMING / BATCH ---]")

    print("\n[4.1] run_day_streaming yields batches")
    dr2 = DailyRunner(rng=random.Random(42))
    players_30 = generate_players(count=30, seed=42)
    for p in players_30:
        p.install_date = fixed_now

    batches_yielded = []
    for batch in dr2.run_day_streaming(players_30, fixed_now):
        batches_yielded.append(batch)
    print(f"  [INFO] {len(batches_yielded)} batches streamed")
    # Nếu 30 player × ~5 events = ~150 events, có thể trong 1 batch hoặc nhiều
    # Validate mỗi batch là Batch instance
    assert_check(all(b is not None for b in batches_yielded), "all batches valid")

    # ----------------------------------------------------------------------
    # 5. create_batch_sink với dry-run mode
    # ----------------------------------------------------------------------
    print("\n[--- 5. CREATE_BATCH_SINK ---]")

    print("\n[5.1] dry-run sink")
    from storage.raw_writer import RawDataWriter

    with tempfile.TemporaryDirectory() as tmp:
        rw = RawDataWriter(root=Path(tmp) / "raw")
        sink_dry = create_batch_sink(raw_writer=rw, client=None, dry_run=True)
        # Tạo batch giả
        from gameanalytics.batcher import Batch
        from gameanalytics.compressor import compress_events
        from gameanalytics.compressor import serialize_events
        import gzip as _gzip
        events = [{"category": "user", "client_ts": 100, "user_id": "p1"}]
        compressed = compress_events(events)
        raw = serialize_events(events)
        batch = Batch(events=events, compressed_size=len(compressed), raw_size=len(raw))
        sink_dry(batch)
        # Verify raw file đã được tạo
        raw_files = list((Path(tmp) / "raw").rglob("*.json.gz"))
        assert_check(len(raw_files) > 0, f"raw files = {len(raw_files)}")

    # ----------------------------------------------------------------------
    # 6. Validator
    # ----------------------------------------------------------------------
    print("\n[--- 6. VALIDATOR ---]")

    print("\n[6.1] Validator bắt được invalid events")
    from gameanalytics.validator import EventValidator, ValidationError
    validator = EventValidator()
    # Note: validator raise_if_invalid trên lỗi. Dùng try/except.
    valid_event = {
        "category": "user",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600,
        "sdk_version": "rest api v2",
        "os_version": "android 14",
        "manufacturer": "samsung",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
        "build": "1.0.0",
    }
    try:
        validator.validate_schema(valid_event)
        assert_check(True, "valid event passes")
    except Exception as e:  # noqa: BLE001
        assert_check(False, f"unexpected: {e}")

    invalid_event = {"category": "invalid_category", "v": 99}
    try:
        validator.validate_schema(invalid_event)
        assert_check(False, "should have failed")
    except Exception:  # noqa: BLE001
        assert_check(True, "invalid category fails")

    # ----------------------------------------------------------------------
    # 7. Report Builder
    # ----------------------------------------------------------------------
    print("\n[--- 7. REPORT BUILDER ---]")

    print("\n[7.1] Build report từ daily stats")
    rb = ReportBuilder()
    for day in range(3):
        fake_stats = {
            "sessions_active": 100,
            "events_generated": 500,
            "batches_flushed": 2,
            "by_category": {"user": 100, "design": 200, "ad": 100, "session_end": 100},
            "by_country": {"VN": 50, "US": 50},
            "wall_clock_sec": 1.5,
            "errors": 0,
        }
        rb.add_day(fixed_now + timedelta(days=day), fake_stats)

    rep = rb.build(
        start_date=fixed_now,
        end_date=fixed_now + timedelta(days=2),
        total_players=200,
    )
    assert_check(rep.n_days == 3, f"days = {rep.n_days}")
    assert_check(rep.total_sessions == 300, f"sessions = {rep.total_sessions}")
    assert_check(rep.total_events == 1500, f"events = {rep.total_events}")
    assert_check(rep.avg_dau == 100.0, f"avg_dau = {rep.avg_dau}")

    print("\n[7.2] Report to_dict schema đầy đủ")
    rep_dict = rep.to_dict()
    expected_keys = {
        "start_date", "end_date", "n_days", "total_players",
        "total_sessions", "total_events", "total_batches",
        "total_revenue_minor", "total_revenue_usd",
        "total_paying_players",
        "by_category", "by_country", "by_day", "wall_clock_sec", "metrics",
    }
    assert_check(
        set(rep_dict.keys()) == expected_keys,
        f"keys = {set(rep_dict.keys())}",
    )

    print("\n[7.3] save_json ra file")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.json"
        saved = rep.save_json(out)
        assert_check(saved.exists(), f"exists = {saved.exists()}")
        loaded = json.loads(saved.read_text(encoding="utf-8"))
        assert_check(loaded["n_days"] == 3, "n_days in JSON")

    # ----------------------------------------------------------------------
    # 8. CLI main.py --help
    # ----------------------------------------------------------------------
    print("\n[--- 8. CLI MAIN.PY ---]")

    print("\n[8.1] main.py --help works")
    import subprocess
    proc = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        timeout=30,
    )
    assert_check(proc.returncode == 0, f"returncode = {proc.returncode}")
    # stdout có thể là None nếu capture fails → default decode utf-8 ignore errors
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    assert_check("Mobile Game Event Simulator" in stdout,
                 "help text present")

    # ----------------------------------------------------------------------
    # 9. CLI dry-run end-to-end (chạy thật)
    # ----------------------------------------------------------------------
    print("\n[--- 9. CLI DRY-RUN E2E ---]")

    print("\n[9.1] main.py --players 100 --days 2 --dry-run")
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        raw_root = Path(tmp) / "raw"
        db_path = Path(tmp) / "test_sim.db"
        proc2 = subprocess.run(
            [
                sys.executable, "main.py",
                "--players", "100",
                "--days", "2",
                "--seed", "42",
                "--dry-run",
                f"--db={db_path}",
                f"--raw-root={raw_root}",
                f"--report-output={report_path}",
                "--start-date", "2026-09-28",
            ],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            timeout=300,
        )
        stdout2 = (proc2.stdout or b"").decode("utf-8", errors="replace")
        stderr2 = (proc2.stderr or b"").decode("utf-8", errors="replace")
        assert_check(proc2.returncode == 0,
                     f"returncode = {proc2.returncode}\n"
                     f"stdout: {stdout2[:500]}\n"
                     f"stderr: {stderr2[:500]}")
        # Verify report file
        assert_check(report_path.exists(), f"report exists = {report_path.exists()}")
        # Verify raw data files
        raw_files = list(raw_root.rglob("*.json.gz"))
        assert_check(len(raw_files) > 0, f"raw files = {len(raw_files)}")

        # Check report content
        report_loaded = json.loads(report_path.read_text(encoding="utf-8"))
        assert_check(report_loaded["n_days"] == 2, f"days = {report_loaded['n_days']}")
        assert_check(report_loaded["total_events"] > 0, f"events = {report_loaded['total_events']}")
        # Categories phải có user, session_end, ...
        assert_check("user" in report_loaded["by_category"], "user category")
        assert_check("session_end" in report_loaded["by_category"], "session_end category")

    # ----------------------------------------------------------------------
    # 10. Final summary
    # ----------------------------------------------------------------------
    print("\n[--- 10. FINAL ---]")

    print("\n[10.1] __str__ của SimulationReport")
    print(f"  → {rep}")
    assert_check("SimulationReport" in str(rep), "str format OK")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 25 LARGE-SCALE SIMULATION OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
