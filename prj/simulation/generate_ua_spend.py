"""Generate UA Spend CSV dataset – tiện ích CLI.

Theo BƯỚC 24 INSTRUCTION.md.

Usage::

    python simulation/generate_ua_spend.py --days 30 --output data/ua_spend/ua_spend.csv

Args:
    --days        Số ngày cần generate (default 30).
    --start-date  Ngày bắt đầu (YYYY-MM-DD, default today UTC).
    --seed        Random seed (default 42).
    --output      Đường dẫn output CSV (default data/ua_spend/ua_spend.csv).
    --no-header   Không ghi header row.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from simulation.ua_spend import create_ua_spend_generator  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate UA Spend dataset CSV",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Số ngày (default 30).",
    )
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="Ngày bắt đầu YYYY-MM-DD (default today UTC).",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (default 42).",
    )
    parser.add_argument(
        "--output", type=str, default="data/ua_spend/ua_spend.csv",
        help="Đường dẫn output CSV.",
    )
    parser.add_argument(
        "--no-header", action="store_true",
        help="Không ghi header row.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    else:
        start_date = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )

    print(f"[INFO] Generate UA Spend:")
    print(f"  - Days       : {args.days}")
    print(f"  - Start date : {start_date.strftime('%Y-%m-%d')}")
    print(f"  - Seed       : {args.seed}")
    print(f"  - Output     : {args.output}")

    gen = create_ua_spend_generator(rng_seed=args.seed)
    records = gen.generate(start_date=start_date, n_days=args.days)

    output_path = gen.save_csv(
        records, args.output, include_header=not args.no_header,
    )

    # Stats summary
    stats = gen.compute_stats(records)
    print(f"\n[INFO] Dataset stats:")
    print(f"  - Records       : {stats.total_records}")
    print(f"  - Total spend   : ${stats.total_spend:,.2f}")
    print(f"  - Total installs: {stats.total_installs:,}")
    print(f"  - Blended CPI   : ${stats.blended_cpi:.4f}")
    print(f"  - Blended CTR   : {stats.blended_ctr:.4f}")
    print(f"  - Blended CVR   : {stats.blended_cvr:.4f}")
    print(f"  - By source:")
    for source, agg in stats.by_source.items():
        cpi = agg["spend"] / agg["installs"] if agg["installs"] > 0 else 0
        print(f"      {source:>14}: spend=${agg['spend']:,.2f}, "
              f"installs={agg['installs']:,}, CPI=${cpi:.4f}")

    print(f"\n[INFO] Saved to: {output_path}")


if __name__ == "__main__":
    main()
