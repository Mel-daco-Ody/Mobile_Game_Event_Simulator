"""Simulation Report – tổng kết sau khi chạy simulation.

Theo BƯỚC 25 INSTRUCTION.md:

    Sau khi simulation chạy xong, cần summary các metrics chính:
        - DAU / WAU / MAU
        - ARPU / ARPPU
        - Sessions per day
        - Events per day
        - ROAS (nếu có UA spend)
        - Retention cohorts
        - Churn rate

Module cung cấp:

    - ``DailyReport``: report 1 ngày
    - ``SimulationReport``: tổng kết toàn bộ
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from models import Player


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class DailyReport:
    """Report cho 1 ngày simulation.

    Attributes:
        date: ISO date string.
        active_players: số player active hôm đó.
        new_sessions: số session mới được tạo.
        events_generated: tổng events sinh ra.
        batches_flushed: số batch đã flush.
        by_category: dict {category: count}.
        by_country: dict {country: count}.
        wall_clock_sec: thời gian xử lý (giây).
        errors: số lỗi trong session của player.
    """

    date: str
    active_players: int = 0
    new_sessions: int = 0
    events_generated: int = 0
    batches_flushed: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_country: dict[str, int] = field(default_factory=dict)
    wall_clock_sec: float = 0.0
    errors: int = 0
    revenue_minor: int = 0  # Tổng IAP amount (minor unit) trong ngày

    @property
    def avg_events_per_session(self) -> float:
        if self.new_sessions == 0:
            return 0.0
        return self.events_generated / self.new_sessions

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimulationReport:
    """Tổng kết toàn bộ simulation.

    Attributes:
        start_date: ISO date bắt đầu.
        end_date: ISO date kết thúc.
        n_days: số ngày đã chạy.
        total_players: tổng player ban đầu.
        total_sessions: tổng sessions tạo.
        total_events: tổng events sinh.
        total_batches: tổng batches flushed.
        total_revenue_minor: tổng revenue (minor unit).
        by_category: tổng {category: count}.
        by_country: tổng {country: count}.
        by_day: list DailyReport theo từng ngày.
        wall_clock_sec: tổng thời gian xử lý.
    """

    start_date: str = ""
    end_date: str = ""
    n_days: int = 0
    total_players: int = 0
    total_sessions: int = 0
    total_events: int = 0
    total_batches: int = 0
    total_revenue_minor: int = 0
    total_paying_players: int = 0  # Số player distinct đã thực hiện IAP
    by_category: dict[str, int] = field(default_factory=dict)
    by_country: dict[str, int] = field(default_factory=dict)
    by_day: list[DailyReport] = field(default_factory=list)
    wall_clock_sec: float = 0.0

    # ----------------------------------------
    # Derived metrics
    # ----------------------------------------
    @property
    def avg_dau(self) -> float:
        """Average DAU (Daily Active Users)."""
        if self.n_days == 0:
            return 0.0
        return sum(d.active_players for d in self.by_day) / self.n_days

    @property
    def total_revenue_usd(self) -> float:
        """Total revenue USD (minor → dollar)."""
        return self.total_revenue_minor / 100.0

    @property
    def arpu(self) -> float:
        """ARPU = total revenue / total players."""
        if self.total_players == 0:
            return 0.0
        return self.total_revenue_usd / self.total_players

    @property
    def arppu(self) -> float:
        """ARPPU = total revenue / paying players.

        Trả về None nếu chưa có paying player.
        """
        if self.total_paying_players == 0:
            return None
        return self.total_revenue_usd / self.total_paying_players

    @property
    def events_per_second(self) -> float:
        """Throughput (events / giây)."""
        if self.wall_clock_sec <= 0:
            return 0.0
        return self.total_events / self.wall_clock_sec

    @property
    def avg_events_per_session(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.total_events / self.total_sessions

    # ----------------------------------------
    # Serialization
    # ----------------------------------------
    def to_dict(self) -> dict[str, Any]:
        arppu_val = self.arppu
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "n_days": self.n_days,
            "total_players": self.total_players,
            "total_sessions": self.total_sessions,
            "total_events": self.total_events,
            "total_batches": self.total_batches,
            "total_revenue_minor": self.total_revenue_minor,
            "total_revenue_usd": round(self.total_revenue_usd, 2),
            "total_paying_players": self.total_paying_players,
            "by_category": dict(self.by_category),
            "by_country": dict(self.by_country),
            "by_day": [d.to_dict() for d in self.by_day],
            "wall_clock_sec": round(self.wall_clock_sec, 3),
            "metrics": {
                "avg_dau": round(self.avg_dau, 2),
                "arpu_usd": round(self.arpu, 4),
                "arppu_usd": (round(arppu_val, 4)
                              if arppu_val is not None else None),
                "events_per_second": round(self.events_per_second, 2),
                "avg_events_per_session": round(self.avg_events_per_session, 2),
            },
        }

    def save_json(self, output_path: str | Path) -> Path:
        """Lưu report ra JSON file."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path

    def __str__(self) -> str:
        return (
            f"SimulationReport(days={self.n_days}, "
            f"events={self.total_events:,}, "
            f"sessions={self.total_sessions:,}, "
            f"revenue=${self.total_revenue_usd:,.2f}, "
            f"avg_dau={self.avg_dau:.1f}, "
            f"throughput={self.events_per_second:.0f} events/s)"
        )


# =========================================================================
# ReportBuilder
# =========================================================================

class ReportBuilder:
    """Build report từ aggregate stats + daily stats.

    Usage::

        builder = ReportBuilder()
        for day_offset in range(n_days):
            daily_stats = runner.run_day(...)
            builder.add_day(current_date, daily_stats)
        report = builder.build(start_date, end_date, players)
    """

    def __init__(self) -> None:
        self._daily_reports: list[DailyReport] = []
        self._aggregate = {
            "sessions": 0,
            "events": 0,
            "batches": 0,
            "errors": 0,
            "revenue_minor": 0,
            "paying_players": set(),
            "by_category": Counter(),
            "by_country": Counter(),
            "by_day_active": Counter(),
        }

    def add_day(
        self,
        current_date: datetime,
        daily_stats: dict[str, Any],
    ) -> DailyReport:
        """Thêm 1 daily stats vào builder.

        Args:
            current_date: datetime UTC.
            daily_stats: dict stats từ DailyRunner.run_day.

        Returns:
            DailyReport vừa tạo.
        """
        report = DailyReport(
            date=current_date.strftime("%Y-%m-%d"),
            active_players=daily_stats["sessions_active"],
            new_sessions=daily_stats["sessions_active"],
            events_generated=daily_stats["events_generated"],
            batches_flushed=daily_stats["batches_flushed"],
            by_category=dict(daily_stats.get("by_category", {})),
            by_country=dict(daily_stats.get("by_country", {})),
            wall_clock_sec=daily_stats.get("wall_clock_sec", 0.0),
            errors=daily_stats.get("errors", 0),
            revenue_minor=daily_stats.get("revenue_minor", 0),
        )
        self._daily_reports.append(report)

        # Aggregate
        self._aggregate["sessions"] += report.new_sessions
        self._aggregate["events"] += report.events_generated
        self._aggregate["batches"] += report.batches_flushed
        self._aggregate["errors"] += report.errors
        self._aggregate["revenue_minor"] += report.revenue_minor
        # Union paying players distinct qua các ngày
        for uid in daily_stats.get("paying_players", set()):
            self._aggregate["paying_players"].add(uid)
        for cat, cnt in report.by_category.items():
            self._aggregate["by_category"][cat] += cnt
        for country, cnt in report.by_country.items():
            self._aggregate["by_country"][country] += cnt

        return report

    def build(
        self,
        start_date: datetime,
        end_date: datetime,
        total_players: int,
        aggregate_stats: dict[str, Any] | None = None,
    ) -> SimulationReport:
        """Build SimulationReport từ aggregate.

        Có 2 chế độ:
            1. aggregate_stats is None (default, standalone use):
                Build từ self._aggregate (tự accumulate qua add_day).
                Phù hợp cho unit test, smoke test, hoặc caller không
                có SimulationRunner.
            2. aggregate_stats is dict (production use):
                Build từ aggregate_stats do SimulationRunner.run() cung cấp.
                Đây là SINGLE SOURCE OF TRUTH cho totals — tránh duplicate
                aggregate và đảm bảo consistency giữa SimulationRunner và
                SimulationReport.

        Args:
            start_date: datetime UTC bắt đầu.
            end_date: datetime UTC kết thúc (inclusive).
            total_players: tổng số player trong simulation.
            aggregate_stats: optional dict từ SimulationRunner.get_stats().

        Returns:
            SimulationReport.
        """
        if aggregate_stats is not None:
            # Production mode: dùng aggregate từ SimulationRunner (single source)
            total_sessions = aggregate_stats.get(
                "total_sessions", self._aggregate["sessions"]
            )
            total_events = aggregate_stats.get(
                "total_events", self._aggregate["events"]
            )
            total_batches = aggregate_stats.get(
                "total_batches", self._aggregate["batches"]
            )
            total_revenue_minor = aggregate_stats.get(
                "total_revenue_minor", self._aggregate["revenue_minor"]
            )
            total_payers = aggregate_stats.get(
                "total_paying_players",
                len(self._aggregate["paying_players"]),
            )
            # Safety wrap: aggregate_stats từ SimulationRunner.run() đã là
            # dict, nhưng nếu caller truyền partial dict có Counter thì
            # wrap lại để đảm bảo type đúng.
            by_category_src = aggregate_stats.get(
                "by_category", self._aggregate["by_category"]
            )
            by_category = (
                dict(by_category_src)
                if not isinstance(by_category_src, dict)
                else by_category_src
            )
            by_country_src = aggregate_stats.get(
                "by_country", self._aggregate["by_country"]
            )
            by_country = (
                dict(by_country_src)
                if not isinstance(by_country_src, dict)
                else by_country_src
            )
            wall_clock_sec = aggregate_stats.get(
                "wall_clock_sec",
                sum(d.wall_clock_sec for d in self._daily_reports),
            )
        else:
            # Standalone mode: dùng self._aggregate
            total_sessions = self._aggregate["sessions"]
            total_events = self._aggregate["events"]
            total_batches = self._aggregate["batches"]
            total_revenue_minor = self._aggregate["revenue_minor"]
            total_payers = len(self._aggregate["paying_players"])
            by_category = dict(self._aggregate["by_category"])
            by_country = dict(self._aggregate["by_country"])
            wall_clock_sec = sum(d.wall_clock_sec for d in self._daily_reports)

        return SimulationReport(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            n_days=len(self._daily_reports),
            total_players=total_players,
            total_sessions=total_sessions,
            total_events=total_events,
            total_batches=total_batches,
            total_revenue_minor=total_revenue_minor,
            total_paying_players=total_payers,
            by_category=by_category,
            by_country=by_country,
            by_day=list(self._daily_reports),
            wall_clock_sec=wall_clock_sec,
        )


__all__ = [
    "DailyReport",
    "ReportBuilder",
    "SimulationReport",
]
