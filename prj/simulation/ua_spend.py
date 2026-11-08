"""UA Spend Simulator – generate chi tiêu User Acquisition dataset.

Theo BƯỚC 24 INSTRUCTION.md:

    **Mục tiêu:** Bổ sung dữ liệu chi tiêu acquisition để tính ROAS.

    Tạo ``data/ua_spend/ua_spend.csv`` với schema::

        date, platform, country, source, campaign,
        spend, impressions, clicks, installs

    **UA Spend KHÔNG phải player purchase event** — đây là nguồn riêng.

    Sau khi có cả Game Events + UA Spend, có thể tính::

        CPI    = spend / installs
        ROAS   = revenue / spend
        LTV:CAC
        Payback

    Acquisition sources mô phỏng::

        organic, facebook, google_ads, tiktok

Module cung cấp:

    - ``UASpendRecord``: 1 dòng CSV (date, platform, country, source, campaign,
                        spend, impressions, clicks, installs)
    - ``UASpendGenerator``: generate dataset cho N ngày
    - ``DailyUASpend``: 1 ngày UA spend (list các record)
    - ``ROASAnalyzer``: tính CPI / CTR / CVR / ROAS từ dataset
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from config import ua_spend_config
from config.ua_spend_config import (
    CPI_CEILING,
    CPI_FLOOR,
    CSV_DELIMITER,
    CSV_SCHEMA,
    CTR_RANGES,
    CVR_RANGES,
    DATE_FORMAT,
    DEFAULT_DATE_RANGE_DAYS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_OUTPUT_FILE,
    PLATFORM_SPEND_MULTIPLIER,
    SPEND_RANGES_USD,
    UA_CAMPAIGNS,
    UA_COUNTRIES,
    UA_COUNTRY_WEIGHTS,
    UA_PLATFORMS,
    UA_PLATFORM_WEIGHTS,
    UA_SOURCES,
    UA_SOURCE_WEIGHTS,
)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class UASpendRecord:
    """1 dòng CSV: spend cho (date, platform, country, source, campaign).

    Attributes:
        date: ISO date string "YYYY-MM-DD".
        platform: "android" | "ios".
        country: country code (2 letters).
        source: "organic" | "facebook" | "google_ads" | "tiktok".
        campaign: campaign ID hoặc None (organic).
        spend: USD spend hôm đó.
        impressions: số impression.
        clicks: số click.
        installs: số install (cần consistency với player count).
    """

    date: str
    platform: str
    country: str
    source: str
    campaign: str | None
    spend: float
    impressions: int
    clicks: int
    installs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_csv_row(self) -> list[Any]:
        """Trả về list theo đúng thứ tự CSV_SCHEMA."""
        return [
            self.date,
            self.platform,
            self.country,
            self.source,
            self.campaign if self.campaign is not None else "",
            round(self.spend, 2),
            self.impressions,
            self.clicks,
            self.installs,
        ]

    @property
    def cpi(self) -> float:
        """Cost Per Install (USD / install)."""
        if self.installs <= 0:
            return 0.0
        return self.spend / self.installs

    @property
    def ctr(self) -> float:
        """Click-through rate."""
        if self.impressions <= 0:
            return 0.0
        return self.clicks / self.impressions

    @property
    def cvr(self) -> float:
        """Conversion rate (installs / clicks)."""
        if self.clicks <= 0:
            return 0.0
        return self.installs / self.clicks

    def __str__(self) -> str:
        return (
            f"UASpend({self.date} {self.platform}/{self.country}/"
            f"{self.source}@{self.campaign}: "
            f"spend=${self.spend:.2f}, installs={self.installs})"
        )


@dataclass
class DailyUASpend:
    """Tổng UA spend của 1 ngày.

    Attributes:
        date: ISO date string.
        records: list các UASpendRecord trong ngày.
        total_spend: tổng spend USD.
        total_installs: tổng installs.
    """

    date: str
    records: list[UASpendRecord] = field(default_factory=list)

    @property
    def total_spend(self) -> float:
        return sum(r.spend for r in self.records)

    @property
    def total_installs(self) -> int:
        return sum(r.installs for r in self.records)

    @property
    def total_impressions(self) -> int:
        return sum(r.impressions for r in self.records)

    @property
    def total_clicks(self) -> int:
        return sum(r.clicks for r in self.records)

    @property
    def weighted_cpi(self) -> float:
        if self.total_installs == 0:
            return 0.0
        return self.total_spend / self.total_installs

    def records_by_source(self) -> dict[str, list[UASpendRecord]]:
        out: dict[str, list[UASpendRecord]] = defaultdict(list)
        for r in self.records:
            out[r.source].append(r)
        return out

    def __str__(self) -> str:
        return (
            f"DailyUASpend({self.date}: "
            f"{len(self.records)} records, "
            f"spend=${self.total_spend:.2f}, "
            f"installs={self.total_installs})"
        )


# Default dict cho type hints
from collections import defaultdict  # noqa: E402


@dataclass
class UASpendStats:
    """Thống kê tổng hợp của UA spend dataset."""

    total_records: int = 0
    total_spend: float = 0.0
    total_installs: int = 0
    total_impressions: int = 0
    total_clicks: int = 0

    # Per-source aggregation
    by_source: dict[str, dict[str, float]] = field(default_factory=dict)

    # Per-platform aggregation
    by_platform: dict[str, dict[str, float]] = field(default_factory=dict)

    # Per-country aggregation
    by_country: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def blended_cpi(self) -> float:
        """Trung bình CPI toàn dataset."""
        if self.total_installs == 0:
            return 0.0
        return self.total_spend / self.total_installs

    @property
    def blended_ctr(self) -> float:
        """Trung bình CTR toàn dataset."""
        if self.total_impressions == 0:
            return 0.0
        return self.total_clicks / self.total_impressions

    @property
    def blended_cvr(self) -> float:
        """Trung bình CVR toàn dataset."""
        if self.total_clicks == 0:
            return 0.0
        return self.total_installs / self.total_clicks


# =========================================================================
# Generator
# =========================================================================

class UASpendGenerator:
    """Generate UA Spend dataset synthetic.

    Usage::

        gen = UASpendGenerator(rng_seed=42)
        dataset = gen.generate(
            start_date=date(2026, 9, 1),
            n_days=30,
        )
        gen.save_csv(dataset, "data/ua_spend/ua_spend.csv")
    """

    def __init__(
        self,
        rng: random.Random | None = None,
        # Override defaults cho test
        sources: tuple[str, ...] | None = None,
        platforms: tuple[str, ...] | None = None,
        countries: tuple[str, ...] | None = None,
        spend_ranges: dict[str, tuple[float, float]] | None = None,
        ctr_ranges: dict[str, tuple[float, float]] | None = None,
        cvr_ranges: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Khởi tạo UASpendGenerator.

        Args:
            rng: Random instance.
            sources: override acquisition sources.
            platforms: override platforms.
            countries: override countries.
            spend_ranges: override spend ranges.
            ctr_ranges: override CTR ranges.
            cvr_ranges: override CVR ranges.
        """
        self._rng = rng or random.Random()
        self._sources = sources or UA_SOURCES
        self._platforms = platforms or UA_PLATFORMS
        self._countries = countries or UA_COUNTRIES
        self._spend_ranges = spend_ranges or dict(SPEND_RANGES_USD)
        self._ctr_ranges = ctr_ranges or dict(CTR_RANGES)
        self._cvr_ranges = cvr_ranges or dict(CVR_RANGES)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(
        self,
        start_date: date_type | datetime | str,
        n_days: int = DEFAULT_DATE_RANGE_DAYS,
        daily_seed_offset: int = 0,
    ) -> list[UASpendRecord]:
        """Generate dataset cho N ngày.

        Args:
            start_date: ngày bắt đầu (date / datetime / str "YYYY-MM-DD").
            n_days: số ngày cần generate.
            daily_seed_offset: offset để advance rng mỗi ngày
                              (tránh bị trùng output giữa các ngày).

        Returns:
            list UASpendRecord (flat, đã sort theo date).
        """
        start = self._coerce_date(start_date)

        all_records: list[UASpendRecord] = []
        for day_offset in range(n_days):
            current_date = start + timedelta(days=day_offset)
            daily_records = self.generate_daily(
                current_date,
                rng_seed=(daily_seed_offset + day_offset),
            )
            all_records.extend(daily_records)

        # Sort theo date rồi source
        all_records.sort(key=lambda r: (r.date, r.source, r.platform, r.country))
        return all_records

    def generate_daily(
        self,
        current_date: date_type | datetime | str,
        rng_seed: int | None = None,
    ) -> list[UASpendRecord]:
        """Generate UA spend cho 1 ngày duy nhất.

        Args:
            current_date: ngày cần generate.
            rng_seed: seed riêng cho ngày này (None = dùng rng hiện tại).

        Returns:
            list UASpendRecord trong ngày.
        """
        date_str = self._format_date(current_date)

        # Tạo rng riêng cho ngày này (deterministic)
        if rng_seed is not None:
            day_rng = random.Random(rng_seed)
        else:
            day_rng = self._rng

        records: list[UASpendRecord] = []
        for source in self._sources:
            # Mỗi source pick campaign(s)
            campaigns = UA_CAMPAIGNS.get(source, (None,))
            for campaign in campaigns:
                # Mỗi campaign pick platform(s)
                platforms = self._pick_platforms(day_rng)
                for platform in platforms:
                    # Mỗi platform pick country(s)
                    countries = self._pick_countries(day_rng)
                    for country in countries:
                        record = self._generate_record(
                            date_str, source, platform, country,
                            campaign, day_rng,
                        )
                        records.append(record)

        return records

    def save_csv(
        self,
        records: list[UASpendRecord] | list[DailyUASpend],
        output_path: str | Path,
        include_header: bool = True,
    ) -> Path:
        """Lưu records ra CSV file.

        Args:
            records: list UASpendRecord hoặc list DailyUASpend.
            output_path: đường dẫn file output.
            include_header: True = ghi header.

        Returns:
            Path tuyệt đối của file đã ghi.
        """
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        flat: list[UASpendRecord] = []
        for r in records:
            if isinstance(r, UASpendRecord):
                flat.append(r)
            elif isinstance(r, DailyUASpend):
                flat.extend(r.records)

        # Sort theo schema order
        flat.sort(key=lambda x: (x.date, x.source, x.platform, x.country))

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=CSV_DELIMITER)
            if include_header:
                writer.writerow(CSV_SCHEMA)
            for record in flat:
                writer.writerow(record.to_csv_row())

        return path

    def load_csv(
        self,
        csv_path: str | Path,
    ) -> list[UASpendRecord]:
        """Đọc CSV ngược lại thành list UASpendRecord.

        Args:
            csv_path: đường dẫn file CSV.

        Returns:
            list UASpendRecord.
        """
        records: list[UASpendRecord] = []
        path = Path(csv_path)
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                campaign = row["campaign"] or None
                records.append(UASpendRecord(
                    date=row["date"],
                    platform=row["platform"],
                    country=row["country"],
                    source=row["source"],
                    campaign=campaign,
                    spend=float(row["spend"]),
                    impressions=int(row["impressions"]),
                    clicks=int(row["clicks"]),
                    installs=int(row["installs"]),
                ))
        return records

    def compute_stats(
        self,
        records: Iterable[UASpendRecord],
    ) -> UASpendStats:
        """Tính stats tổng hợp.

        Args:
            records: iterable UASpendRecord.

        Returns:
            UASpendStats.
        """
        records = list(records)
        stats = UASpendStats(total_records=len(records))

        for r in records:
            stats.total_spend += r.spend
            stats.total_installs += r.installs
            stats.total_impressions += r.impressions
            stats.total_clicks += r.clicks

            # Per-source
            if r.source not in stats.by_source:
                stats.by_source[r.source] = {
                    "spend": 0.0, "installs": 0,
                    "impressions": 0, "clicks": 0,
                }
            s = stats.by_source[r.source]
            s["spend"] += r.spend
            s["installs"] += r.installs
            s["impressions"] += r.impressions
            s["clicks"] += r.clicks

            # Per-platform
            if r.platform not in stats.by_platform:
                stats.by_platform[r.platform] = {
                    "spend": 0.0, "installs": 0,
                    "impressions": 0, "clicks": 0,
                }
            p = stats.by_platform[r.platform]
            p["spend"] += r.spend
            p["installs"] += r.installs
            p["impressions"] += r.impressions
            p["clicks"] += r.clicks

            # Per-country
            if r.country not in stats.by_country:
                stats.by_country[r.country] = {
                    "spend": 0.0, "installs": 0,
                    "impressions": 0, "clicks": 0,
                }
            c = stats.by_country[r.country]
            c["spend"] += r.spend
            c["installs"] += r.installs
            c["impressions"] += r.impressions
            c["clicks"] += r.clicks

        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _pick_platforms(
        self,
        day_rng: random.Random,
    ) -> list[str]:
        """Pick platform(s) theo trọng số (mỗi record có 1 platform)."""
        # For UA we keep ALL platforms but weight differently.
        # Đơn giản hoá: 1 record = 1 platform, pick 1.
        return [day_rng.choices(
            self._platforms, weights=list(UA_PLATFORM_WEIGHTS), k=1,
        )[0]]

    def _pick_countries(
        self,
        day_rng: random.Random,
    ) -> list[str]:
        """Pick country(s) theo trọng số."""
        return [day_rng.choices(
            self._countries, weights=list(UA_COUNTRY_WEIGHTS), k=1,
        )[0]]

    def _generate_record(
        self,
        date_str: str,
        source: str,
        platform: str,
        country: str,
        campaign: str | None,
        day_rng: random.Random,
    ) -> UASpendRecord:
        """Tạo 1 UASpendRecord.

        Quy tắc:
            - organic: spend = 0, impressions = 0, clicks = 0, chỉ có installs
            - paid:    spend → pick per source/platform
                       impressions = spend / CPM * 1000
                       clicks = impressions * CTR
                       installs = clicks * CVR
        """
        if source == "organic":
            # Organic: không tốn tiền, không có funnel
            installs = self._sample_organic_installs(day_rng)
            return UASpendRecord(
                date=date_str,
                platform=platform,
                country=country,
                source=source,
                campaign=None,
                spend=0.0,
                impressions=0,
                clicks=0,
                installs=installs,
            )

        # Paid source
        spend_range = self._spend_ranges.get(source, (100.0, 1000.0))
        spend = day_rng.uniform(*spend_range)
        # Apply platform multiplier
        multiplier = PLATFORM_SPEND_MULTIPLIER.get(platform, 1.0)
        spend *= multiplier

        # CTR → clicks
        ctr_range = self._ctr_ranges.get(source, (0.01, 0.05))
        ctr = day_rng.uniform(*ctr_range)

        # CVR → installs
        cvr_range = self._cvr_ranges.get(source, (0.10, 0.30))
        cvr = day_rng.uniform(*cvr_range)

        # CPM (cost per mille impressions) ~ $5-$20 mobile ads
        cpm = day_rng.uniform(5.0, 20.0)
        # impressions = (spend / cpm) * 1000
        impressions = max(1, int((spend / cpm) * 1000))
        clicks = max(0, int(impressions * ctr))
        installs = max(0, int(clicks * cvr))

        # Clamp CPI range
        if installs > 0:
            cpi = spend / installs
            if cpi < CPI_FLOOR:
                # Need to boost installs
                installs = max(1, int(spend / CPI_FLOOR))
            elif cpi > CPI_CEILING:
                # Too expensive — cap installs
                installs = max(1, int(spend / CPI_CEILING))
        else:
            installs = max(1, int(spend / max(CPI_FLOOR, 1.0)))

        return UASpendRecord(
            date=date_str,
            platform=platform,
            country=country,
            source=source,
            campaign=campaign,
            spend=round(spend, 2),
            impressions=impressions,
            clicks=clicks,
            installs=installs,
        )

    @staticmethod
    def _sample_organic_installs(rng: random.Random) -> int:
        """Sample organic installs (0 → không spam)."""
        # Organic: 50% chance có installs, mỗi ngày khoảng 5-30 organic installs
        if rng.random() < 0.5:
            return 0
        return rng.randint(5, 30)

    @staticmethod
    def _format_date(d: date_type | datetime | str) -> str:
        if isinstance(d, str):
            return d
        if isinstance(d, datetime):
            return d.strftime(DATE_FORMAT)
        return d.strftime(DATE_FORMAT)

    @staticmethod
    def _coerce_date(d: date_type | datetime | str) -> date_type:
        if isinstance(d, str):
            return datetime.strptime(d, DATE_FORMAT).date()
        if isinstance(d, datetime):
            return d.date()
        return d


# =========================================================================
# ROAS Analyzer
# =========================================================================

class ROASAnalyzer:
    """Tính toán CPI / ROAS / LTV:CAC / Payback từ UA Spend + Revenue.

    Usage::

        analyzer = ROASAnalyzer()
        cpi = analyzer.compute_cpi(record)
        roas = analyzer.compute_roas(revenue=10000.0, spend=2000.0)
        report = analyzer.analyze(records, total_revenue=50000.0)
    """

    @staticmethod
    def compute_cpi(record: UASpendRecord) -> float:
        """Cost Per Install."""
        return record.cpi

    @staticmethod
    def compute_ctr(record: UASpendRecord) -> float:
        return record.ctr

    @staticmethod
    def compute_cvr(record: UASpendRecord) -> float:
        return record.cvr

    @staticmethod
    def compute_roas(revenue: float, spend: float) -> float:
        """Return On Ad Spend.

        Args:
            revenue: tổng USD revenue (from business events).
            spend: tổng USD UA spend.

        Returns:
            ROAS = revenue / spend. 0 nếu spend = 0.
        """
        if spend <= 0:
            return 0.0
        return revenue / spend

    @staticmethod
    def compute_cac(spend: float, installs: int) -> float:
        """Customer Acquisition Cost (USD / install)."""
        if installs <= 0:
            return 0.0
        return spend / installs

    @staticmethod
    def compute_ltv_cac_ratio(
        ltv: float,
        cac: float,
    ) -> float:
        """LTV:CAC ratio. Standard: ≥ 3.0 là khoẻ."""
        if cac <= 0:
            return 0.0
        return ltv / cac

    @staticmethod
    def compute_payback_months(
        monthly_revenue_per_user: float,
        cac: float,
    ) -> float:
        """Số tháng để hoàn vốn (payback period).

        Args:
            monthly_revenue_per_user: ARPU/tháng (USD).
            cac: CAC (USD).

        Returns:
            Số tháng để recover CAC. math.inf nếu ARPU = 0.
        """
        if monthly_revenue_per_user <= 0:
            return math.inf
        return cac / monthly_revenue_per_user

    def analyze(
        self,
        records: list[UASpendRecord],
        total_revenue: float = 0.0,
    ) -> dict[str, Any]:
        """Phân tích tổng hợp UA spend + revenue.

        Args:
            records: list UASpendRecord.
            total_revenue: tổng USD revenue từ business events.

        Returns:
            dict các metrics.
        """
        total_spend = sum(r.spend for r in records)
        total_installs = sum(r.installs for r in records)

        # Per-source ROAS (giả định revenue phân bổ đều theo installs)
        roas_by_source: dict[str, float] = {}
        for source in UA_SOURCES:
            source_records = [r for r in records if r.source == source]
            source_spend = sum(r.spend for r in source_records)
            source_installs = sum(r.installs for r in source_records)
            if source_installs > 0 and total_revenue > 0 and total_installs > 0:
                # Revenue allocated by install share
                share = source_installs / total_installs
                source_rev = total_revenue * share
                roas_by_source[source] = (
                    source_rev / source_spend if source_spend > 0 else 0.0
                )
            else:
                roas_by_source[source] = 0.0

        cac = self.compute_cac(total_spend, total_installs)
        roas = self.compute_roas(total_revenue, total_spend)
        blended_cpi = total_spend / total_installs if total_installs > 0 else 0.0

        return {
            "total_spend": round(total_spend, 2),
            "total_installs": total_installs,
            "total_revenue": round(total_revenue, 2),
            "blended_cpi": round(blended_cpi, 4),
            "blended_cac": round(cac, 4),
            "blended_roas": round(roas, 4),
            "roas_by_source": {k: round(v, 4) for k, v in roas_by_source.items()},
            "records_count": len(records),
        }


# =========================================================================
# Convenience helpers
# =========================================================================

def create_ua_spend_generator(rng_seed: int | None = None) -> UASpendGenerator:
    """Factory: tạo UASpendGenerator với optional deterministic seed."""
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    return UASpendGenerator(rng=rng)


__all__ = [
    "DailyUASpend",
    "ROASAnalyzer",
    "UASpendGenerator",
    "UASpendRecord",
    "UASpendStats",
    "create_ua_spend_generator",
]
