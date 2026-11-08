"""Smoke test cho BƯỚC 3 – Player Generator.

Kiểm tra:
    1. Sinh đúng số lượng player theo `count`.
    2. user_id ổn định, đúng format 'player_NNNNNN' (zero-padded 6 chữ số).
    3. Cùng seed -> cùng kết quả (tái lập được).
    4. Phân bổ segment xấp xỉ đúng weights từ player_config.
    5. user_id KHÔNG trùng nhau trong cùng một batch.
    6. Acquisition 'organic' luôn có campaign_id = None.
    7. Install_date nằm trong khoảng simulation_start_date - 30 ngày.

Chạy:
    cd prj && python generators/smoke_test.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone

# UTF-8 cho stdout + đảm bảo project root nằm trong sys.path
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import player_config  # noqa: E402
from generators import (  # noqa: E402
    generate_one_player,
    generate_players,
    summarize_players,
)


def assert_check(condition: bool, label: str) -> None:
    """In kết quả kiểm tra và raise nếu sai."""
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    """Chạy toàn bộ check cho Player Generator."""
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 3: PLAYER GENERATOR")
    print("=" * 60)

    sim_start = datetime(2026, 9, 1, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # 1. Sinh đúng số lượng
    # ------------------------------------------------------------------
    print("\n[1] generate_players(count=100) trả đúng 100 player")
    players = generate_players(count=100, simulation_start_date=sim_start, seed=42)
    assert_check(len(players) == 100, f"len(players) = {len(players)}")

    # ------------------------------------------------------------------
    # 2. user_id đúng format và KHÔNG trùng nhau
    # ------------------------------------------------------------------
    print("\n[2] user_id đúng format 'player_NNNNNN' và không trùng")
    ids = [p.user_id for p in players]
    assert ids[0] == "player_000001", f"first id = {ids[0]}"
    assert ids[-1] == "player_000100", f"last id = {ids[-1]}"
    assert_check(len(set(ids)) == 100, "100 user_id duy nhất")

    # Format check: 'player_' + 6 chữ số
    for uid in ids[:5]:
        assert uid.startswith("player_") and len(uid) == len("player_NNNNNN"), \
            f"format sai: {uid}"
    print(f"  [OK] 5 user_id đầu: {ids[:5]}")

    # ------------------------------------------------------------------
    # 3. Tái lập được với cùng seed
    # ------------------------------------------------------------------
    print("\n[3] Cùng seed -> cùng kết quả (reproducible)")
    p_a = generate_players(count=10, simulation_start_date=sim_start, seed=123)
    p_b = generate_players(count=10, simulation_start_date=sim_start, seed=123)
    for a, b in zip(p_a, p_b):
        assert a.user_id == b.user_id
        assert a.player_segment == b.player_segment
        assert a.country == b.country
        assert a.install_date == b.install_date
    print("  [OK] 10 player khớp hoàn toàn giữa 2 lần sinh")

    # ------------------------------------------------------------------
    # 4. Seed khác -> kết quả khác
    # ------------------------------------------------------------------
    print("\n[4] Seed khác -> phân bổ khác (không bị 'đóng băng')")
    p_c = generate_players(count=10, simulation_start_date=sim_start, seed=999)
    same = sum(1 for a, c in zip(p_a, p_c)
               if a.player_segment == c.player_segment and a.country == c.country)
    assert_check(same < 10, f"ít nhất 1 player khác biệt (same={same}/10)")

    # ------------------------------------------------------------------
    # 5. Phân bổ segment xấp xỉ weights
    # ------------------------------------------------------------------
    print("\n[5] Phân bổ segment xấp xỉ weights (N=2000, tolerance 5%)")
    big = generate_players(count=2000, simulation_start_date=sim_start, seed=7)
    summary = summarize_players(big)

    def check_distribution(name: str, segments: list[str],
                           weights: list[float], counts: dict[str, int]) -> None:
        total = sum(counts.values())
        for seg, w in zip(segments, weights):
            actual = counts.get(seg, 0) / total
            # Tolerance rộng 5% cho segment nhỏ (whale ~2%, high_retention ~15%)
            tolerance = max(0.05, w * 0.2)
            diff = abs(actual - w)
            ok = diff <= tolerance
            print(f"    {name}.{seg:20s} actual={actual:.3f}  "
                  f"expected={w:.3f}  diff={diff:.3f}  tol={tolerance:.3f}  "
                  f"[{'OK' if ok else 'FAIL'}]")
            if not ok:
                raise AssertionError(
                    f"{name}.{seg} actual={actual:.3f} != expected={w:.3f}"
                )

    print("  -> payer_segment:")
    check_distribution("payer",
                       player_config.PAYER_SEGMENTS,
                       player_config.PAYER_SEGMENT_WEIGHTS,
                       summary["payer"])
    print("  -> engagement_segment:")
    check_distribution("engagement",
                       player_config.ENGAGEMENT_SEGMENTS,
                       player_config.ENGAGEMENT_SEGMENT_WEIGHTS,
                       summary["engagement"])
    print("  -> lifecycle_segment:")
    check_distribution("lifecycle",
                       player_config.LIFECYCLE_SEGMENTS,
                       player_config.LIFECYCLE_SEGMENT_WEIGHTS,
                       summary["lifecycle"])
    print("  -> acquisition_source:")
    check_distribution("acquisition_source",
                       player_config.ACQUISITION_SOURCES,
                       player_config.ACQUISITION_SOURCE_WEIGHTS,
                       summary["acquisition_source"])

    # ------------------------------------------------------------------
    # 6. Acquisition 'organic' luôn có campaign_id = None
    # ------------------------------------------------------------------
    print("\n[6] organic luôn có campaign_id = None")
    for p in big:
        if p.acquisition_source == "organic":
            assert p.campaign_id is None, \
                f"organic có campaign_id không hợp lệ: {p.campaign_id}"
    print("  [OK] Mọi organic player đều campaign_id = None")

    # Paid sources phải có campaign_id
    paid_without_campaign = [
        p for p in big
        if p.acquisition_source != "organic" and p.campaign_id is None
    ]
    assert_check(len(paid_without_campaign) == 0,
                 f"Paid source nào cũng có campaign_id "
                 f"(missing={len(paid_without_campaign)})")

    # ------------------------------------------------------------------
    # 7. Install_date nằm trong khoảng [sim_start - 30 ngày, sim_start]
    # ------------------------------------------------------------------
    print("\n[7] install_date trong [sim_start - 30 ngày, sim_start]")
    from datetime import timedelta
    earliest = sim_start - timedelta(days=30)
    out_of_range = [
        p for p in big
        if p.install_date < earliest or p.install_date > sim_start
    ]
    assert_check(len(out_of_range) == 0,
                 f"Tất cả install_date hợp lệ (out_of_range={len(out_of_range)})")

    # ------------------------------------------------------------------
    # 8. generate_one_player tạo được 1 player đúng format
    # ------------------------------------------------------------------
    print("\n[8] generate_one_player(idx=42) hoạt động độc lập")
    import random as _random
    solo = generate_one_player(idx=42, simulation_start_date=sim_start,
                                rng=_random.Random(0))
    assert_check(solo.user_id == "player_000042",
                 f"user_id = {solo.user_id}")

    # ------------------------------------------------------------------
    # 9. Default counter bằng 0 (chưa chơi session nào)
    # ------------------------------------------------------------------
    print("\n[9] Player mới sinh có counter = 0")
    p0 = players[0]
    assert_check(p0.session_count == 0, "session_count = 0")
    assert_check(p0.transaction_num == 0, "transaction_num = 0")
    assert_check(p0.lifetime_revenue == 0, "lifetime_revenue = 0")
    assert_check(p0.is_new_install is True, "is_new_install = True")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 3 PLAYER GENERATOR OK")
    print("=" * 60)

    # In tóm tắt phân bổ để dễ quan sát
    print("\n[Tóm tắt phân bổ N=10000]")
    big_summary = summarize_players(
        generate_players(count=10_000, simulation_start_date=sim_start, seed=2026)
    )
    for k, v in big_summary.items():
        total = sum(v.values())
        print(f"  {k}:")
        for seg, n in sorted(v.items(), key=lambda x: -x[1]):
            pct = 100 * n / total
            print(f"    {seg:20s} {n:6d} ({pct:5.1f}%)")


if __name__ == "__main__":
    main()
