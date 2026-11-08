"""Quick check: client_ts spread across simulation window."""
import gzip
import json
import glob
from datetime import datetime, timezone
from collections import Counter

import os
base = r"c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj\data\raw"
files = []
for root, _, fnames in os.walk(base):
    for fn in fnames:
        if fn.endswith(".json.gz"):
            files.append(os.path.join(root, fn))
files.sort()

print(f"Total batch files: {len(files)}")

by_day = Counter()
unique_client_ts = set()
sample_events = []
for f in files:
    with gzip.open(f, "rt") as fh:
        data = json.loads(fh.read())
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events:
            ts = ev.get("client_ts")
            if ts is None:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc)
            day_key = d.strftime("%Y-%m-%d")
            by_day[day_key] += 1
            unique_client_ts.add(ts)
            if len(sample_events) < 5:
                sample_events.append((ev.get("category"), ts, day_key))

print(f"\n=== Events by day (UTC) ===")
for day in sorted(by_day.keys()):
    print(f"  {day}: {by_day[day]:4d} events")

print(f"\n=== Sample events (first 5) ===")
for cat, ts, day in sample_events:
    print(f"  cat={cat:12s} client_ts={ts} ({day} {datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')})")

print(f"\n=== Stats ===")
print(f"  Unique client_ts : {len(unique_client_ts)}")
if unique_client_ts:
    print(f"  Min client_ts    : {min(unique_client_ts)} ({datetime.fromtimestamp(min(unique_client_ts), tz=timezone.utc)})")
    print(f"  Max client_ts    : {max(unique_client_ts)} ({datetime.fromtimestamp(max(unique_client_ts), tz=timezone.utc)})")
    span_days = (max(unique_client_ts) - min(unique_client_ts)) / 86400
    print(f"  Span             : {span_days:.2f} days")
