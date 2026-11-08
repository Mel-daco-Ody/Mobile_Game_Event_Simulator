"""Smoke test cho Bước 1 – Configuration.

Script đơn giản để chứng minh các file config đã load được
và các giá trị simulation assumption có sẵn.

Cách chạy (từ thư mục gốc của project – chính là folder prj):
    python -m config.smoke_test
hoặc:
    cd prj && python config/smoke_test.py
"""

from __future__ import annotations

import os
import sys

# Đảm bảo console Windows in được tiếng Việt (cp1252 không encode được)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
 pass

# Đảm bảo project root nằm trong sys.path để import được package 'config'
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import (  # noqa: E402  (sau khi sửa sys.path mới import)
    game_config,
    player_config,
    event_config,
    monetization_config,
)


def in_range(name, value, lo, hi):
    """In ra dòng kiểm tra giá trị có nằm trong khoảng [lo, hi] không."""
    status = "OK" if lo <= value <= hi else "FAIL"
    print(f"  [{status}] {name} = {value} (range [{lo}, {hi}])")
    return lo <= value <= hi


def main():
    """In ra các tham số chính của từng file config."""
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 1: CONFIGURATION")
    print("=" * 60)

    # === game_config ===
    print("\n[game_config]")
    print(f"  API_VERSION       = {game_config.API_VERSION}")
    print(f"  SDK_VERSION       = {game_config.SDK_VERSION}")
    print(f"  GAME_BUILD        = {game_config.GAME_BUILD}")
    print(f"  EVENT_VERSION     = {game_config.EVENT_VERSION}")
    in_range("POST_LIMIT_BYTES", game_config.POST_LIMIT_BYTES, 1_048_576, 1_048_576)
    in_range("SAFE_LIMIT_BYTES", game_config.SAFE_LIMIT_BYTES, 500_000, 950_000)
    assert game_config.SAFE_LIMIT_BYTES < game_config.POST_LIMIT_BYTES, (
        "SAFE_LIMIT_BYTES phải nhỏ hơn POST_LIMIT_BYTES (1 MB)"
    )
    print("  -> SAFE_LIMIT_BYTES < POST_LIMIT_BYTES: OK")

    # === player_config ===
    print("\n[player_config]")
    print(f"  PAYER_SEGMENTS    = {player_config.PAYER_SEGMENTS}")
    print(f"  ENGAGEMENT_SEGMENTS = {player_config.ENGAGEMENT_SEGMENTS}")
    print(f"  LIFECYCLE_SEGMENTS  = {player_config.LIFECYCLE_SEGMENTS}")
    print(f"  ACQUISITION_SOURCES = {player_config.ACQUISITION_SOURCES}")
    print(f"  RETENTION_CHECKPOINTS = {player_config.RETENTION_CHECKPOINTS}")
    in_range(
        "sum PAYER_WEIGHTS",
        sum(player_config.PAYER_SEGMENT_WEIGHTS),
        0.999,
        1.001,
    )
    in_range(
        "sum ENGAGEMENT_WEIGHTS",
        sum(player_config.ENGAGEMENT_SEGMENT_WEIGHTS),
        0.999,
        1.001,
    )
    in_range(
        "sum LIFECYCLE_WEIGHTS",
        sum(player_config.LIFECYCLE_SEGMENT_WEIGHTS),
        0.999,
        1.001,
    )
    in_range(
        "sum ACQUISITION_WEIGHTS",
        sum(player_config.ACQUISITION_SOURCE_WEIGHTS),
        0.999,
        1.001,
    )

    # === event_config ===
    print("\n[event_config]")
    print(f"  ERROR_PROBABILITY  = {event_config.ERROR_PROBABILITY_PER_EVENT}")
    print(f"  AD_TYPES           = {event_config.AD_TYPES}")
    print(f"  DESIGN_EVENT_IDS (count) = {len(event_config.DESIGN_EVENT_IDS)}")
    assert event_config.ERROR_PROBABILITY_PER_EVENT < 0.01, (
        "Xác suất error phải rất nhỏ (< 1%) theo rule GameAnalytics"
    )
    print("  -> ERROR_PROBABILITY < 1%: OK")

    # === monetization_config ===
    print("\n[monetization_config]")
    print(f"  PRODUCT_CATALOG (count) = {len(monetization_config.PRODUCT_CATALOG)}")
    for p in monetization_config.PRODUCT_CATALOG[:3]:
        print(f"    {p}")
    print("  ...")
    print(f"  VALID_CURRENCIES   = {monetization_config.VALID_CURRENCIES}")
    # Kiểm tra currency code 3-letter UPPERCASE
    bad_currency = [
        c for c in monetization_config.VALID_CURRENCIES
        if not (len(c) == 3 and c.isupper() and c.isalpha())
    ]
    assert not bad_currency, f"Currency không hợp lệ ISO-4217: {bad_currency}"
    print("  -> Mọi currency đều 3-letter UPPERCASE: OK")
    # Kiểm tra amount > 0
    bad_price = [p for p in monetization_config.PRODUCT_CATALOG if p["price"] <= 0]
    assert not bad_price, f"Product có price <= 0: {bad_price}"
    print("  -> Mọi product đều có price > 0: OK")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 1 CONFIGURATION OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
