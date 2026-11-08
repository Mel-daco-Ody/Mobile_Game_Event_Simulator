"""Verify timestamps in raw files - look at first 3 events per file across all 30 days."""
import os, gzip, json
from datetime import datetime, timezone
from collections import Counter

base = r"c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj\data\raw"
files = []
for root, _, fnames in os.walk(base):
    for fn in fnames:
        if fn.endswith(".json.gz"):
            files.append(os.path.join(root, fn))
files.sort()

# Check distribution across all days (FOLDER STRUCTURE)
folder_days = Counter()
for f in files:
    rel = f.replace(base, "")
    # Extract day from path like \year=2026\month=09\day=29\
    parts = rel.split("\\")
    day_part = [p for p in parts if p.startswith("day=")]
    if day_part:
        d = day_part[0].replace("day=", "")
        folder_days[d] += 1

print("=== Folder structure (date partition) ===")
for d in sorted(folder_days.keys()):
    print(f"  day={d}: {folder_days[d]} batch files")

# Read all client_ts from all files
print("\n=== client_ts distribution (actual event timestamps) ===")
all_client_ts = []
for f in files:
    with gzip.open(f, "rt") as fh:
        data = json.loads(fh.read())
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events:
            ts = ev.get("client_ts")
            if ts is not None:
                all_client_ts.append(ts)

# Group by date
by_date = Counter()
for ts in all_client_ts:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    by_date[d.strftime("%Y-%m-%d")] += 1

print(f"Total events: {len(all_client_ts)}")
print(f"Days with events: {len(by_date)}")
for d in sorted(by_date.keys()):
    print(f"  {d}: {by_date[d]:4d} events")

# Min/max
if all_client_ts:
    min_ts = min(all_client_ts)
    max_ts = max(all_client_ts)
    print(f"\n  Min client_ts: {min_ts} = {datetime.fromtimestamp(min_ts, tz=timezone.utc)}")
    print(f"  Max client_ts: {max_ts} = {datetime.fromtimestamp(max_ts, tz=timezone.utc)}")
    print(f"  Span: {(max_ts - min_ts) / 86400:.2f} days")

# Mismatch check
print("\n=== Mismatch between folder date vs client_ts ===")
mismatches = 0
for f in files:
    rel = f.replace(base, "")
    parts = rel.split("\\")
    day_part = [p for p in parts if p.startswith("day=")]
    folder_day = day_part[0].replace("day=", "") if day_part else "?"
    with gzip.open(f, "rt") as fh:
        data = json.loads(fh.read())
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events:
            ts = ev.get("client_ts")
            if ts is None:
                continue
            actual_day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            # Convert folder_day to YYYY-MM-DD
            try:
                y, m, d = folder_day.split("-")
                folder_date_str = f"20{y}-{m}-{d}"
            except ValueError:
                folder_date_str = folder_day
            if actual_day != folder_date_str:
                mismatches += 1
                if mismatches <= 5:
                    print(f"  MISMATCH: file={folder_day}, event client_ts date={actual_day}, ts={ts}")
print(f"\nTotal mismatches: {mismatches}")
