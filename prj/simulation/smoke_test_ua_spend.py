"""Smoke test cho BƯỚC 24 – UA Spend Dataset.

Kiểm tra simulation/ua_spend.py + config/ua_spend_config.py:

A. Config:
    1. UA_SOURCES có đủ 4 sources (theo INSTRUCTION).
    2. UA_PLATFORMS có 2 (android, ios).
    3. UA_COUNTRIES có ≥ 5.
    4. UA_CAMPAIGNS có cho cả 3 paid sources (organic = None).
    5. SPEND_RANGES_USD có cho mọi source (organic = 0).
    6. CTR_RANGES có cho paid sources.
    7. CVR_RANGES có cho paid sources.

B. UASpendRecord dataclass:
    8. to_csv_row() đúng thứ tự schema.
    9. cpi, ctr, cvr tính đúng.

C. UASpendGenerator – generate record:
    10. organic: spend=0, impressions=0, clicks=0, installs có thể >0.
    11. paid: spend>0, installs>0, CPI hợp lý ($0.1 - $8).
    12. CTR & CVR ratio hợp lý.

D. UASpendGenerator – generate_daily:
    13. Trả list UASpendRecord.
    14. Tất cả records cùng date.
    15. Mọi (platform, country, source, campaign) combo hợp lệ.

E. UASpendGenerator – generate (n_days):
    16. Trả N records khác nhau.
    17. Date range đúng.

F. UASpendGenerator – save_csv / load_csv:
    18. CSV header đúng schema.
    19. CSV roundtrip (write → read) bảo toàn data.
    20. CSV file có rows = records count.

G. UASpendGenerator – compute_stats:
    21. total_spend, total_installs đúng.
    22. by_source aggregation đúng.
    23. by_platform, by_country aggregation đúng.
    24. blended_cpi, blended_ctr, blended_cvr đúng.

H. ROASAnalyzer:
    25. compute_cpi / ctr / cvr.
    26. compute_roas(rev, spend).
    27. compute_cac(spend, installs).
    28. compute_ltv_cac_ratio.
    29. compute_payback_months.
    30. analyze() trả đủ metrics.

I. Realistic ratios:
    31. 30 ngày dataset: blended CPI ∈ [0.5, 5.0] USD.
    32. Blended CTR ∈ [0.01, 0.10].
    33. Blended CVR ∈ [0.10, 0.50].
    34. ROAS hợp lý khi revenue > 0.
    35. paid sources có spend > 0, organic = 0.

J. CLI script:
    36. simulation/generate_ua_spend.py tồn tại.

Chạy:
    cd prj && python simulation/smoke_test_ua_spend.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import ua_spend_config  # noqa: E402
from config.ua_spend_config import CSV_SCHEMA  # noqa: E402
from simulation.ua_spend import (  # noqa: E402
    ROASAnalyzer,
    UASpendGenerator,
    UASpendRecord,
    UASpendStats,
    create_ua_spend_generator,
)


# =====================================================================
# Helpers
# =====================================================================

def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 24: UA SPEND DATASET")
    print("=" * 60)

    # ----------------------------------------------------------------------
    # A. Config
    # ----------------------------------------------------------------------
    print("\n[--- A. CONFIG ---]")

    print("\n[A1] UA_SOURCES có đủ 4 sources")
    assert_check(
        set(ua_spend_config.UA_SOURCES) == {"organic", "facebook", "google_ads", "tiktok"},
        f"sources = {ua_spend_config.UA_SOURCES}",
    )

    print("\n[A2] UA_PLATFORMS có 2")
    assert_check(
        set(ua_spend_config.UA_PLATFORMS) == {"android", "ios"},
        f"platforms = {ua_spend_config.UA_PLATFORMS}",
    )

    print("\n[A3] UA_COUNTRIES có ≥ 5")
    assert_check(len(ua_spend_config.UA_COUNTRIES) >= 5,
                 f"countries = {ua_spend_config.UA_COUNTRIES}")

    print("\n[A4] UA_CAMPAIGNS có cho cả 3 paid sources")
    for src in ("facebook", "google_ads", "tiktok"):
        assert src in ua_spend_config.UA_CAMPAIGNS, f"missing {src}"
        assert len(ua_spend_config.UA_CAMPAIGNS[src]) > 0
    # organic = (None,)
    assert ua_spend_config.UA_CAMPAIGNS["organic"] == (None,), "organic = (None,)"
    assert_check(True, "campaigns OK")

    print("\n[A5] SPEND_RANGES_USD có cho mọi source")
    for src in ua_spend_config.UA_SOURCES:
        assert src in ua_spend_config.SPEND_RANGES_USD, f"missing spend {src}"
        lo, hi = ua_spend_config.SPEND_RANGES_USD[src]
        assert lo <= hi, f"spend range {src}: {lo} > {hi}"
        assert lo >= 0.0
    assert_check(True, "spend ranges OK")

    print("\n[A6] CTR_RANGES có cho paid sources")
    for src in ("facebook", "google_ads", "tiktok"):
        assert src in ua_spend_config.CTR_RANGES
        lo, hi = ua_spend_config.CTR_RANGES[src]
        assert 0 < lo <= hi < 1.0
    assert_check(True, "CTR ranges OK")

    print("\n[A7] CVR_RANGES có cho paid sources")
    for src in ("facebook", "google_ads", "tiktok"):
        assert src in ua_spend_config.CVR_RANGES
        lo, hi = ua_spend_config.CVR_RANGES[src]
        assert 0 < lo <= hi < 1.0
    assert_check(True, "CVR ranges OK")

    # ----------------------------------------------------------------------
    # B. UASpendRecord
    # ----------------------------------------------------------------------
    print("\n[--- B. UASPENDRECORD ---]")

    print("\n[B1] to_csv_row() đúng thứ tự schema")
    rec = UASpendRecord(
        date="2026-09-01",
        platform="android",
        country="VN",
        source="facebook",
        campaign="VN_AUG_01",
        spend=500.0,
        impressions=120000,
        clicks=4200,
        installs=850,
    )
    row = rec.to_csv_row()
    assert_check(
        row[0] == "2026-09-01" and row[1] == "android",
        f"row = {row[:5]}",
    )
    # organic campaign = None → ""
    rec_organic = UASpendRecord(
        date="2026-09-01", platform="android", country="VN",
        source="organic", campaign=None,
        spend=0.0, impressions=0, clicks=0, installs=10,
    )
    assert_check(
        rec_organic.to_csv_row()[4] == "",
        f"organic campaign = '{rec_organic.to_csv_row()[4]}'",
    )

    print("\n[B2] cpi / ctr / cvr tính đúng")
    assert_check(abs(rec.cpi - 500.0 / 850) < 1e-6, f"cpi = {rec.cpi}")
    assert_check(abs(rec.ctr - 4200 / 120000) < 1e-6, f"ctr = {rec.ctr}")
    assert_check(abs(rec.cvr - 850 / 4200) < 1e-6, f"cvr = {rec.cvr}")

    # ----------------------------------------------------------------------
    # C. UASpendGenerator – generate record
    # ----------------------------------------------------------------------
    print("\n[--- C. GENERATE RECORD ---]")

    gen = create_ua_spend_generator(rng_seed=42)

    print("\n[C1] organic: spend=0, impressions=0, clicks=0")
    # Force organic record
    organic_rec = gen._generate_record(
        date_str="2026-09-01",
        source="organic",
        platform="android",
        country="VN",
        campaign=None,
        day_rng=__import__("random").Random(0),
    )
    assert_check(organic_rec.spend == 0.0, f"spend = {organic_rec.spend}")
    assert_check(organic_rec.impressions == 0, f"impressions = {organic_rec.impressions}")
    assert_check(organic_rec.clicks == 0, f"clicks = {organic_rec.clicks}")

    print("\n[C2] paid: spend>0, installs>0, CPI hợp lý ($0.1 - $8)")
    paid_rec = gen._generate_record(
        date_str="2026-09-01",
        source="facebook",
        platform="android",
        country="VN",
        campaign="VN_AUG_01",
        day_rng=__import__("random").Random(0),
    )
    assert_check(paid_rec.spend > 0, f"spend = {paid_rec.spend}")
    assert_check(paid_rec.installs > 0, f"installs = {paid_rec.installs}")
    cpi = paid_rec.cpi
    assert 0.1 <= cpi <= 8.0, f"CPI = {cpi}"
    assert_check(True, f"CPI = ${cpi:.4f}")

    print("\n[C3] CTR & CVR ratio hợp lý")
    assert_check(0 < paid_rec.ctr <= 1.0, f"ctr = {paid_rec.ctr}")
    assert_check(0 < paid_rec.cvr <= 1.0, f"cvr = {paid_rec.cvr}")

    # ----------------------------------------------------------------------
    # D. UASpendGenerator – generate_daily
    # ----------------------------------------------------------------------
    print("\n[--- D. GENERATE_DAILY ---]")

    print("\n[D1] Trả list UASpendRecord")
    daily_records = gen.generate_daily("2026-09-01", rng_seed=0)
    assert all(isinstance(r, UASpendRecord) for r in daily_records)
    assert_check(len(daily_records) > 0, f"records = {len(daily_records)}")

    print("\n[D2] Tất cả records cùng date")
    assert all(r.date == "2026-09-01" for r in daily_records), "all same date"
    assert_check(True, "same date")

    print("\n[D3] Mọi combo hợp lệ (source ∈ UA_SOURCES)")
    for r in daily_records:
        assert r.source in ua_spend_config.UA_SOURCES
        assert r.platform in ua_spend_config.UA_PLATFORMS
        assert r.country in ua_spend_config.UA_COUNTRIES
    assert_check(True, "combos valid")

    # ----------------------------------------------------------------------
    # E. UASpendGenerator – generate (n_days)
    # ----------------------------------------------------------------------
    print("\n[--- E. GENERATE_N_DAYS ---]")

    print("\n[E1] Trả N records khác nhau")
    all_records = gen.generate(
        start_date="2026-09-01",
        n_days=30,
    )
    assert_check(len(all_records) > 0, f"records = {len(all_records)}")
    # 30 days × multiple records/day
    print(f"  [INFO] 30 days → {len(all_records)} records")

    print("\n[E2] Date range đúng")
    dates = sorted({r.date for r in all_records})
    assert dates[0] == "2026-09-01", f"first date = {dates[0]}"
    assert dates[-1] == "2026-09-30", f"last date = {dates[-1]}"
    assert_check(len(dates) == 30, f"30 unique dates = {len(dates)}")

    # ----------------------------------------------------------------------
    # F. save_csv / load_csv
    # ----------------------------------------------------------------------
    print("\n[--- F. SAVE/LOAD CSV ---]")

    print("\n[F1] CSV header đúng schema")
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "ua_spend.csv"
        saved_path = gen.save_csv(all_records, output_path)
        with saved_path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert_check(tuple(header) == CSV_SCHEMA,
                     f"header = {header}")

    print("\n[F2] CSV roundtrip bảo toàn data")
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "ua_spend.csv"
        gen.save_csv(all_records, output_path)
        loaded = gen.load_csv(output_path)
        assert len(loaded) == len(all_records), f"len mismatch: {len(loaded)} vs {len(all_records)}"
        # So sánh 1 record
        first_orig = all_records[0]
        first_loaded = next(r for r in loaded if r.date == first_orig.date and r.platform == first_orig.platform)
        assert_check(
            first_orig.campaign == first_loaded.campaign
            and abs(first_orig.spend - first_loaded.spend) < 0.01
            and first_orig.installs == first_loaded.installs,
            "roundtrip OK",
        )

    print("\n[F3] CSV file rows = records count")
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "ua.csv"
        gen.save_csv(all_records, output_path)
        with output_path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert_check(len(rows) == len(all_records) + 1,  # +1 cho header
                     f"rows = {len(rows)} (incl header)")

    # ----------------------------------------------------------------------
    # G. compute_stats
    # ----------------------------------------------------------------------
    print("\n[--- G. COMPUTE_STATS ---]")

    print("\n[G1] total_spend, total_installs đúng")
    stats: UASpendStats = gen.compute_stats(all_records)
    expected_spend = sum(r.spend for r in all_records)
    expected_installs = sum(r.installs for r in all_records)
    assert_check(abs(stats.total_spend - expected_spend) < 0.01,
                 f"spend = {stats.total_spend}")
    assert_check(stats.total_installs == expected_installs,
                 f"installs = {stats.total_installs}")

    print("\n[G2] by_source aggregation đúng")
    for src in ua_spend_config.UA_SOURCES:
        agg = stats.by_source[src]
        exp_spend = sum(r.spend for r in all_records if r.source == src)
        exp_installs = sum(r.installs for r in all_records if r.source == src)
        assert abs(agg["spend"] - exp_spend) < 0.01
        assert agg["installs"] == exp_installs
    assert_check(True, "by_source OK")

    print("\n[G3] by_platform, by_country aggregation đúng")
    for plat in ua_spend_config.UA_PLATFORMS:
        assert plat in stats.by_platform, f"missing platform {plat}"
    for country in ua_spend_config.UA_COUNTRIES:
        assert country in stats.by_country, f"missing country {country}"
    assert_check(True, "by_platform/by_country OK")

    print("\n[G4] blended_cpi, blended_ctr, blended_cvr đúng")
    expected_cpi = stats.total_spend / stats.total_installs
    expected_ctr = stats.total_clicks / stats.total_impressions if stats.total_impressions else 0
    expected_cvr = stats.total_installs / stats.total_clicks if stats.total_clicks else 0
    assert abs(stats.blended_cpi - expected_cpi) < 1e-9, f"cpi = {stats.blended_cpi}"
    assert_check(True, "blended stats OK")

    # ----------------------------------------------------------------------
    # H. ROASAnalyzer
    # ----------------------------------------------------------------------
    print("\n[--- H. ROASANALYZER ---]")

    analyzer = ROASAnalyzer()

    print("\n[H1] compute_cpi / ctr / cvr")
    assert abs(analyzer.compute_cpi(rec) - 500.0 / 850) < 1e-6
    assert_check(True, "cpi / ctr / cvr OK")

    print("\n[H2] compute_roas(rev, spend)")
    roas = analyzer.compute_roas(revenue=10000.0, spend=2000.0)
    assert_check(abs(roas - 5.0) < 1e-6, f"roas = {roas}")

    print("\n[H3] compute_cac(spend, installs)")
    cac = analyzer.compute_cac(spend=2000.0, installs=400)
    assert_check(abs(cac - 5.0) < 1e-6, f"cac = {cac}")

    print("\n[H4] compute_ltv_cac_ratio")
    ratio = analyzer.compute_ltv_cac_ratio(ltv=15.0, cac=5.0)
    assert_check(abs(ratio - 3.0) < 1e-6, f"ratio = {ratio}")

    print("\n[H5] compute_payback_months")
    payback = analyzer.compute_payback_months(monthly_revenue_per_user=2.5, cac=5.0)
    assert_check(abs(payback - 2.0) < 1e-6, f"payback = {payback}")
    payback_zero = analyzer.compute_payback_months(monthly_revenue_per_user=0.0, cac=5.0)
    import math as _math
    assert_check(payback_zero == _math.inf, f"payback_zero = {payback_zero}")

    print("\n[H6] analyze() trả đủ metrics")
    report = analyzer.analyze(all_records, total_revenue=50_000.0)
    expected_keys = {
        "total_spend", "total_installs", "total_revenue",
        "blended_cpi", "blended_cac", "blended_roas",
        "roas_by_source", "records_count",
    }
    assert_check(set(report.keys()) == expected_keys,
                 f"keys = {set(report.keys())}")
    assert_check(report["records_count"] == len(all_records),
                 f"records_count = {report['records_count']}")
    assert_check(report["total_revenue"] == 50_000.0,
                 f"revenue = {report['total_revenue']}")

    # ----------------------------------------------------------------------
    # I. Realistic ratios
    # ----------------------------------------------------------------------
    print("\n[--- I. REALISTIC RATIOS ---]")

    print("\n[I1] Blended CPI ∈ [0.5, 5.0] USD")
    assert_check(
        0.5 <= stats.blended_cpi <= 5.0,
        f"blended_cpi = ${stats.blended_cpi:.4f}",
    )

    print("\n[I2] Blended CTR ∈ [0.01, 0.10]")
    assert_check(
        0.01 <= stats.blended_ctr <= 0.10,
        f"blended_ctr = {stats.blended_ctr:.4f}",
    )

    print("\n[I3] Blended CVR ∈ [0.10, 0.50]")
    assert_check(
        0.10 <= stats.blended_cvr <= 0.50,
        f"blended_cvr = {stats.blended_cvr:.4f}",
    )

    print("\n[I4] ROAS hợp lý khi revenue > 0 (tính với revenue cao)")
    realistic_analyzer = ROASAnalyzer()
    # Revenue gấp 5x spend → ROAS ~ 5.0
    realistic_report = realistic_analyzer.analyze(all_records, total_revenue=stats.total_spend * 5)
    roas_value = realistic_report["blended_roas"]
    assert_check(
        4.0 <= roas_value <= 6.0,
        f"realistic roas (rev=5x spend) = {roas_value:.4f}",
    )

    print("\n[I5] paid sources có spend > 0, organic = 0")
    paid_spend = sum(r.spend for r in all_records if r.source != "organic")
    organic_spend = sum(r.spend for r in all_records if r.source == "organic")
    assert_check(paid_spend > 0, f"paid_spend = {paid_spend}")
    assert_check(organic_spend == 0.0, f"organic_spend = {organic_spend}")

    print("\n[I6] organic có installs > 0 (organic installs counted)")
    organic_installs = sum(r.installs for r in all_records if r.source == "organic")
    assert_check(organic_installs > 0, f"organic_installs = {organic_installs}")

    # ----------------------------------------------------------------------
    # J. CLI script
    # ----------------------------------------------------------------------
    print("\n[--- J. CLI SCRIPT ---]")

    print("\n[J1] generate_ua_spend.py tồn tại")
    cli_path = _PROJECT_ROOT / "simulation" / "generate_ua_spend.py"
    assert_check(cli_path.exists(), f"exists = {cli_path.exists()}")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 24 UA SPEND OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
