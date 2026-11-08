"""Test chứng minh bug: client_ts của events KHÔNG được spread theo từng ngày.

Bug: _run_player_session() gọi generate_session(player) mà KHÔNG truyền
session_start_ts, nên generate_session() fallback về datetime.now(). Hệ quả:
mọi events sinh ra (kể cả khi simulate ngày 01/09) đều có client_ts ≈ now().

Test này chạy SimulationRunner 5 ngày, capture toàn bộ client_ts, kiểm tra
chúng có spread đều trong 5 ngày hay cluster về 1 ngày (today).
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure prj/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gameanalytics.retry import create_retry_client, RetryPolicy
from gameanalytics.batcher import Batcher
from generators.player_factory import generate_players
from simulation.daily_runner import DailyRunner, SimulationRunner
from simulation.retention import RetentionSimulator


def main() -> int:
    # 5 ngày, 10 players, capture tất cả events
    n_days = 5
    n_players = 10
    # start_date: 5 ngày TRƯỚC today, để kết thúc = today
    today_utc = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    start_date = today_utc - timedelta(days=n_days - 1)
    # Capture buffer: (day_offset, client_ts, event_category)
    captured: list[tuple[int, int, str]] = []

    # Fake retry client: không POST, chỉ capture event
    from gameanalytics.client import GAClient, SendResult
    from unittest.mock import MagicMock

    class CapturingClient(GAClient):
        def __init__(self):
            self.sent_batches = []

        def send_batch(self, batch, request_kind="events", retry_count=0):
            from gameanalytics.client import SendResult
            for ev in batch.events:
                captured.append((None, ev.get("client_ts"), ev.get("category")))
            return SendResult(
                status_code=200,
                timestamp=time.time(),
                retry_count=0,
                payload_size=batch.size_bytes,
                request_kind=request_kind,
            )

        def init(self):
            from gameanalytics.client import InitResult
            return InitResult(
                server_ts=int(time.time()),
                local_ts=int(time.time()),
                offset=0,
                configs_hash="test",
            )

    ga_client = CapturingClient()
    # Disable real HTTP via env
    import os
    os.environ["GA_DRY_RUN"] = "1"

    retry_client = create_retry_client(
        ga_client,
        policy=RetryPolicy(max_retries=0, base_delay=0),
    )

    batcher = Batcher(max_size=500, max_age_sec=999999)

    players = generate_players(
        n=n_players,
        start_date=start_date,
        rng_seed=42,
    )

    daily = DailyRunner(
        rng=None,
        retention_simulator=RetentionSimulator(rng=__import__("random").Random(42)),
        batcher=batcher,
        ga_client=retry_client,
        db_path=None,
    )
    runner = SimulationRunner(daily_runner=daily)

    t0 = time.time()
    runner.run(
        players=players,
        start_date=start_date,
        n_days=n_days,
    )
    elapsed = time.time() - t0

    if not captured:
        print("FAIL: Không capture được event nào")
        return 1

    # Phân tích distribution
    today_ts = today_utc.timestamp()
    yesterday_ts = (today_utc - timedelta(days=1)).timestamp()
    oldest_expected = (today_utc - timedelta(days=n_days - 1)).timestamp()

    # Group theo ngày (UTC)
    by_day: Counter = Counter()
    out_of_range = 0
    for _, ts, cat in captured:
        if ts is None:
            continue
        # Tính day_offset từ start_date
        ev_date = datetime.fromtimestamp(ts, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        day_offset = (ev_date - start_date).days
        by_day[day_offset] += 1
        # Out of range: trước start_date hoặc sau today_utc
        if ts < oldest_expected - 86400 or ts > today_ts + 86400:
            out_of_range += 1

    print(f"\n=== RESULT ===")
    print(f"Captured      : {len(captured)} events")
    print(f"Window        : {start_date.strftime('%Y-%m-%d')} → {today_utc.strftime('%Y-%m-%d')} ({n_days} days)")
    print(f"Elapsed       : {elapsed:.2f}s")
    print(f"By day_offset : {dict(sorted(by_day.items()))}")
    print(f"Out of range  : {out_of_range}")

    # Đánh giá: mong đợi ít nhất 4/5 ngày có events
    days_with_events = sum(1 for v in by_day.values() if v > 0)
    print(f"\nDays w/ events: {days_with_events}/{n_days}")

    if days_with_events < n_days - 1:
        print(f"\n❌ FAIL: Chỉ có {days_with_events} ngày có events (mong đợi ≥ {n_days - 1})")
        print("   → Bug confirmed: timestamps cluster vào 1-2 ngày thay vì spread")
        return 1

    print(f"\n✅ PASS: Timestamps spread across {days_with_events} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
