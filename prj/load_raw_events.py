"""Load raw event files from data/raw/ và phân tích bằng pandas.

Không qua GA, không cần cloud. Đọc thẳng các file .json.gz
trong cấu trúc year=YYYY/month=MM/day=DD/.

Usage:
    python load_raw_events.py
    python load_raw_events.py --from 2026-08-31 --to 2026-09-29
    python load_raw_events.py --export-csv dau.csv
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:
    import pandas as pd
except ImportError:
    print("Cần cài pandas: pip install pandas", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def iter_events(
    root: Path,
    year_from: int | None = None,
    month_from: int | None = None,
    day_from: int | None = None,
    year_to: int | None = None,
    month_to: int | None = None,
    day_to: int | None = None,
) -> Iterator[dict]:
    """Yield events từ các batch .json.gz trong data/raw/.

    Filter theo partition date (year/month/day) nếu truyền vào.
    """
    for fpath in sorted(root.rglob("*.json.gz")):
        # Parse partition từ path: .../year=2026/month=09/day=29/batch_xxx.json.gz
        parts = fpath.parts
        year = month = day = None
        for p in parts:
            if p.startswith("year="):
                year = int(p.split("=")[1])
            elif p.startswith("month="):
                month = int(p.split("=")[1])
            elif p.startswith("day="):
                day = int(p.split("=")[1])

        # Filter
        if year_from is not None and (
            year < year_from
            or (year == year_from and (month_from and month < month_from))
            or (year == year_from and month == month_from and day_from and day < day_from)
        ):
            continue
        if year_to is not None and (
            year > year_to
            or (year == year_to and (month_to and month > month_to))
            or (year == year_to and month == month_to and day_to and day > day_to)
        ):
            continue

        with gzip.open(fpath, "rt", encoding="utf-8") as fh:
            try:
                data = json.loads(fh.read())
            except json.JSONDecodeError as e:
                print(f"  WARN: skip {fpath.name}: {e}", file=sys.stderr)
                continue
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events:
            ev["_source_file"] = fpath.name
            yield ev


def load_dataframe(
    root: Path,
    year_from: int | None = None,
    month_from: int | None = None,
    day_from: int | None = None,
    year_to: int | None = None,
    month_to: int | None = None,
    day_to: int | None = None,
) -> pd.DataFrame:
    """Load tất cả events thành pandas DataFrame.

    Columns chính: client_ts (int), category, user_id, session_id,
                   country, platform, event_date (date), event_hour (int)
    """
    rows = []
    for ev in iter_events(
        root, year_from, month_from, day_from, year_to, month_to, day_to,
    ):
        ts = ev.get("client_ts")
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        rows.append({
            "client_ts": ts,
            "event_date": dt.strftime("%Y-%m-%d"),
            "event_hour": dt.hour,
            "category": ev.get("category"),
            "user_id": ev.get("user_id"),
            "session_id": ev.get("session_id"),
            "session_num": ev.get("session_num"),
            "country": ev.get("country_code"),
            "platform": ev.get("platform"),
            "engagement_segment": ev.get("engagement_segment"),
            # Custom event fields (nếu có)
            "event_id": ev.get("event_id"),
            "amount": ev.get("amount"),
            "item_id": ev.get("item_id"),
            "currency": ev.get("currency"),
            "level": ev.get("level"),
            "world": ev.get("world"),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["event_date"] = pd.to_datetime(df["event_date"])
    return df


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def compute_dau(df: pd.DataFrame) -> pd.DataFrame:
    """Daily Active Users."""
    return (
        df.groupby("event_date")["user_id"]
        .nunique()
        .reset_index()
        .rename(columns={"user_id": "dau"})
        .sort_values("event_date")
    )


def compute_daily_events(df: pd.DataFrame) -> pd.DataFrame:
    """Events per day by category."""
    return (
        df.groupby(["event_date", "category"])
        .size()
        .reset_index(name="event_count")
        .sort_values(["event_date", "category"])
    )


def compute_retention(df: pd.DataFrame) -> pd.DataFrame:
    """Retention D1, D3, D7 dựa trên install_date (first user event).

    Logic:
        install_date = ngày user event 'user' đầu tiên
        D{N} = user có session_end vào install_date + N
    """
    installs = (
        df[df["category"] == "user"]
        .groupby("user_id")["event_date"]
        .min()
        .reset_index()
        .rename(columns={"event_date": "install_date"})
    )

    # Activity by user by date (proxy: có bất kỳ event nào)
    activity = (
        df.groupby(["user_id", "event_date"])
        .size()
        .reset_index(name="n")
    )

    # Merge install_date với activity
    merged = activity.merge(installs, on="user_id")
    merged["day_diff"] = (merged["event_date"] - merged["install_date"]).dt.days

    # Pivot: % retention by day_offset per install cohort
    cohorts = installs.copy()
    cohorts["cohort_size"] = 1
    cohort_sizes = cohorts.groupby("install_date").size().reset_index(name="n_users")

    retention = (
        merged[merged["day_diff"].isin([0, 1, 3, 7])]
        .groupby(["install_date", "day_diff"])["user_id"]
        .nunique()
        .reset_index(name="active_users")
        .merge(cohort_sizes, on="install_date")
    )
    retention["retention_pct"] = (
        retention["active_users"] / retention["n_users"] * 100
    ).round(2)
    return retention.sort_values(["install_date", "day_diff"])


def compute_revenue(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue per day (từ business/IAP events)."""
    iap = df[df["category"] == "business"].copy()
    if iap.empty:
        return pd.DataFrame()
    iap["amount_num"] = pd.to_numeric(iap["amount"], errors="coerce")
    return (
        iap.groupby("event_date")
        .agg(revenue_minor=("amount_num", "sum"),
             paying_users=("user_id", "nunique"))
        .reset_index()
        .sort_values("event_date")
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_date_arg(s: str) -> tuple[int, int, int]:
    """Parse 'YYYY-MM-DD' → (year, month, day)."""
    dt = datetime.strptime(s, "%Y-%m-%d")
    return dt.year, dt.month, dt.day


def main() -> int:
    default_root = Path(__file__).resolve().parent / "data" / "raw"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    parser.add_argument("--export-csv", help="Path export full events CSV")
    parser.add_argument("--top-users", type=int, default=10)
    args = parser.parse_args()

    yf = mf = df_ = None
    yt = mt = dt = None
    if args.date_from:
        yf, mf, df_ = parse_date_arg(args.date_from)
    if args.date_to:
        yt, mt, dt = parse_date_arg(args.date_to)

    print(f"Loading events from: {args.root}")
    df = load_dataframe(args.root, yf, mf, df_, yt, mt, dt)

    if df.empty:
        print("❌ Không có events nào trong range đã chọn.")
        return 1

    print(f"\n[STATS] Total events: {len(df):,}")
    print(f"   Unique users : {df['user_id'].nunique():,}")
    print(f"   Date range   : {df['event_date'].min().date()} -> {df['event_date'].max().date()}")
    print(f"   Span         : {(df['event_date'].max() - df['event_date'].min()).days + 1} days")

    print(f"\n=== Events by category ===")
    print(df["category"].value_counts().to_string())

    print(f"\n=== DAU (top 15) ===")
    dau = compute_dau(df)
    print(dau.head(15).to_string(index=False))

    print(f"\n=== Revenue ===")
    rev = compute_revenue(df)
    if rev.empty:
        print("  (no business/IAP events)")
    else:
        print(rev.to_string(index=False))

    print(f"\n=== Retention D1/D3/D7 (top cohorts) ===")
    ret = compute_retention(df)
    if ret.empty:
        print("  (not enough data)")
    else:
        # Wide format
        wide = ret.pivot(index="install_date", columns="day_diff", values="retention_pct")
        wide.columns = [f"D{c}" for c in wide.columns]
        print(wide.tail(10).round(2).to_string())

    # Export full CSV
    if args.export_csv:
        out = Path(args.export_csv)
        df.drop(columns=["event_date"]).assign(
            event_date=df["event_date"].dt.strftime("%Y-%m-%d")
        ).to_csv(out, index=False)
        print(f"\n[OK] Exported {len(df):,} rows to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
