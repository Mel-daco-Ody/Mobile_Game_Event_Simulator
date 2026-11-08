"""Reproduce error in _run_player_session."""
import sys
import os
sys.path.insert(0, r"c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj")
os.chdir(r"c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj")

import random
import traceback
from datetime import datetime, timezone, timedelta

from generators.player_generator import generate_players
from simulation.daily_runner import DailyRunner
from simulation.retention import RetentionSimulator
from gameanalytics.batcher import Batcher

start_date = datetime(2026, 9, 23, tzinfo=timezone.utc)
players = generate_players(count=10, simulation_start_date=start_date, seed=999)
print(f"Generated {len(players)} players")

rng = random.Random(999)
retention = RetentionSimulator(rng=rng)
batcher = Batcher()
daily = DailyRunner(
    rng=rng,
    retention_simulator=retention,
    batcher=batcher,
)

# Test day 2026-09-23 (offset 0)
current_date = start_date
print(f"\n=== Day {current_date.strftime('%Y-%m-%d')} ===")
for p in players:
    try:
        ctx = daily._run_player_session(p, current_date)
        print(f"  OK {p.user_id}: install_date={p.install_date.strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"  ERR {p.user_id}: {e}")
        traceback.print_exc()
        break
