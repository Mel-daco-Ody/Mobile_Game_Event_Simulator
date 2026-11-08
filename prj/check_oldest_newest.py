import os, gzip, json
from datetime import datetime, timezone
base = r"c:\For_Work_Only\Ongoing_Prj\GAP_v2.0\prj\data\raw"
files = []
for root, _, fnames in os.walk(base):
    for fn in fnames:
        if fn.endswith(".json.gz"):
            files.append(os.path.join(root, fn))
files.sort()

# Read first 3 events from oldest file
if files:
    print(f"Oldest file: {files[0]}")
    with gzip.open(files[0], "rt") as fh:
        data = json.loads(fh.read())
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events[:5]:
            ts = ev.get("client_ts")
            print(f"  cat={ev.get('category')}, client_ts={ts}, UTC={datetime.fromtimestamp(ts, tz=timezone.utc)}")

# Read last 3 events from newest file
if files:
    print(f"\nNewest file: {files[-1]}")
    with gzip.open(files[-1], "rt") as fh:
        data = json.loads(fh.read())
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events[:5]:
            ts = ev.get("client_ts")
            print(f"  cat={ev.get('category')}, client_ts={ts}, UTC={datetime.fromtimestamp(ts, tz=timezone.utc)}")
