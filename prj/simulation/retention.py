"""Retention / Churn Simulator – mô hình hoá lifecycle player.

Theo BƯỚC 23 INSTRUCTION.md:

    **Mục tiêu:** Mô hình hoá lifecycle đầy đủ của player.

    Lifecycle::

        ACQUISITION
          ↓
        INSTALL
          ↓
        FIRST_SESSION
          ↓
        ACTIVATION
          ↓
        ENGAGEMENT
          ↓
        MONETIZATION
          ↓
        RETENTION
          ↓
        CHURN

    Mỗi player có ``install_date``.
    Tính ``player_age = simulation_date - install_date``.

    Cohort & retention checkpoints::

        D1, D3, D7, D14, D30

    Mỗi cohort có retention curve khác nhau
    (Early Churn / Normal / High Retention).

    **Quy tắc:** KHÔNG để tất cả player active mỗi ngày —
    phải có cả churn và dormant.

Module cung cấp:

    - ``RetentionSimulator``: core logic
    - ``RetentionCohort``: nhóm player theo install_date + churn_segment
    - ``RetentionCurve``: curve 5 điểm (D1/D3/D7/D14/D30)
    - ``RetentionStats``: stats cho cohort (active, dormant, churned)
    - ``DailyRetention``: 1 ngày snapshot của cohort
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from config import retention_config
from config.retention_config import (
    ACTIVATION_AGE_DAYS,
    CHURN_STATUS_ACTIVE,
    CHURN_STATUS_CHURNED,
    CHURN_STATUS_DORMANT,
    CHURNED_AFTER_DAYS,
    CHECKPOINT_INTERVALS,
    COHORT_DATE_FORMAT,
    DAILY_ACTIVE_PROBABILITY,
    DORMANT_AFTER_DAYS,
    ENGAGEMENT_AGE_DAYS,
    LIFECYCLE_STAGES,
    LIFECYCLE_TRANSITIONS,
    MONETIZATION_PROBABILITY,
    RETENTION_CHECKPOINTS,
    RETENTION_CURVES,
    RETENTION_THRESHOLD_AGE_DAYS,
)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class RetentionCurve:
    """Curve retention 5 điểm (D1/D3/D7/D14/D30).

    Attributes:
        segment: churn_segment tương ứng ("early_churn" / ...).
        curve: dict {day: retention_rate}.
    """

    segment: str
    curve: dict[int, float]

    @property
    def d1(self) -> float:
        return self.curve.get(1, 0.0)

    @property
    def d3(self) -> float:
        return self.curve.get(3, 0.0)

    @property
    def d7(self) -> float:
        return self.curve.get(7, 0.0)

    @property
    def d14(self) -> float:
        return self.curve.get(14, 0.0)

    @property
    def d30(self) -> float:
        return self.curve.get(30, 0.0)

    def rate_at_day(self, player_age_days: int) -> float:
        """Lấy retention rate tại 1 ngày bất kỳ (linear interpolation).

        Args:
            player_age_days: tuổi player tính theo ngày.

        Returns:
            rate ∈ [0, 1]. Nếu age > max checkpoint → return rate của
            checkpoint cuối cùng (decay). Nếu age < 1 → return 1.0
            (chưa có checkpoint nào).
        """
        if player_age_days <= 0:
            return 1.0

        checkpoints = sorted(RETENTION_CHECKPOINTS)
        max_day = checkpoints[-1]

        if player_age_days >= max_day:
            # Sau D30 → giảm tiếp với exponential decay
            base_rate = self.curve.get(max_day, 0.0)
            extra_days = player_age_days - max_day
            # Decay: base * 0.5^ceil(extra_days / 30)
            return base_rate * (0.5 ** math.ceil(extra_days / 30))

        # Linear interpolation giữa 2 checkpoints gần nhất
        prev_day = 0
        prev_rate = 1.0
        for day in checkpoints:
            if player_age_days <= day:
                day_rate = self.curve[day]
                # Linear interpolate giữa (prev_day, prev_rate) và (day, day_rate)
                if day == prev_day:
                    return day_rate
                fraction = (player_age_days - prev_day) / (day - prev_day)
                return prev_rate + (day_rate - prev_rate) * fraction
            prev_day = day
            prev_rate = self.curve[day]
        return prev_rate

    @classmethod
    def for_segment(cls, segment: str) -> "RetentionCurve":
        """Lấy curve cho 1 segment."""
        if segment not in RETENTION_CURVES:
            raise ValueError(
                f"churn_segment '{segment}' không có trong "
                f"RETENTION_CURVES. Có: {list(RETENTION_CURVES)}"
            )
        return cls(segment=segment, curve=dict(RETENTION_CURVES[segment]))


@dataclass
class RetentionCohort:
    """Nhóm player cùng install_date + churn_segment.

    Attributes:
        install_date: ngày install (date hoặc iso string).
        churn_segment: "early_churn" / "normal_retention" / "high_retention".
        user_ids: list user_id thuộc cohort.
        cohort_size: tổng số player ban đầu.
    """

    install_date: str  # ISO date string "YYYY-MM-DD"
    churn_segment: str
    user_ids: list[str] = field(default_factory=list)

    @property
    def cohort_size(self) -> int:
        return len(self.user_ids)

    @property
    def cohort_key(self) -> str:
        return f"{self.install_date}_{self.churn_segment}"


@dataclass
class DailyRetention:
    """Snapshot retention của 1 cohort tại 1 ngày.

    Attributes:
        cohort_key: "{install_date}_{churn_segment}".
        day: player_age tại thời điểm snapshot.
        cohort_size: tổng player ban đầu.
        active_count: số player active ngày đó.
        dormant_count: số player dormant.
        churned_count: số player churned.
        retention_rate: active_count / cohort_size ∈ [0, 1].
    """

    cohort_key: str
    day: int
    cohort_size: int
    active_count: int
    dormant_count: int
    churned_count: int

    @property
    def retention_rate(self) -> float:
        if self.cohort_size == 0:
            return 0.0
        return self.active_count / self.cohort_size

    @property
    def churn_rate(self) -> float:
        if self.cohort_size == 0:
            return 0.0
        return self.churned_count / self.cohort_size


@dataclass
class RetentionStats:
    """Thống kê retention của toàn bộ simulation."""

    total_players: int = 0
    active_players: int = 0
    dormant_players: int = 0
    churned_players: int = 0

    # Per-cohort size
    cohort_sizes: dict[str, int] = field(default_factory=dict)

    # Per-segment player count
    segment_counts: dict[str, int] = field(default_factory=dict)

    @property
    def active_rate(self) -> float:
        if self.total_players == 0:
            return 0.0
        return self.active_players / self.total_players

    @property
    def churn_rate(self) -> float:
        if self.total_players == 0:
            return 0.0
        return self.churned_players / self.total_players

    def __str__(self) -> str:
        return (
            f"RetentionStats(total={self.total_players}, "
            f"active={self.active_players}, "
            f"dormant={self.dormant_players}, "
            f"churned={self.churned_players}, "
            f"active_rate={self.active_rate:.2%})"
        )


# =========================================================================
# RetentionSimulator
# =========================================================================

class RetentionSimulator:
    """Core logic cho retention / churn.

    Usage::

        sim = RetentionSimulator()
        sim.update_player(player, current_date, rng)

        if sim.is_active_today(player, current_date, rng):
            # Player chơi hôm nay → sinh events
            ...

    Lifecycle transitions::

        update_player() → advance lifecycle stage dựa trên player_age
        is_active_today() → check xem có chơi hôm nay không
        transition_stage() → force stage change (vd: từ first_session → activation)
    """

    def __init__(
        self,
        time_source: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
        # Override defaults (cho test)
        retention_curves: dict[str, dict[int, float]] | None = None,
        daily_active_probability: dict[str, float] | None = None,
        dormant_after_days: int | None = None,
        churned_after_days: int | None = None,
    ) -> None:
        """Khởi tạo RetentionSimulator.

        Args:
            time_source: callable trả datetime UTC.
            rng: Random instance (cho deterministic test).
            retention_curves: override curves.
            daily_active_probability: override active probability.
            dormant_after_days: override DORMANT_AFTER_DAYS.
            churned_after_days: override CHURNED_AFTER_DAYS.
        """
        self._time_source = time_source or (lambda: datetime.now(tz=timezone.utc))
        self._rng = rng or random.Random()

        self._curves = retention_curves or dict(RETENTION_CURVES)
        self._daily_active = daily_active_probability or dict(
            DAILY_ACTIVE_PROBABILITY
        )
        self._dormant_after = (
            dormant_after_days if dormant_after_days is not None
            else DORMANT_AFTER_DAYS
        )
        self._churned_after = (
            churned_after_days if churned_after_days is not None
            else CHURNED_AFTER_DAYS
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def update_player(
        self,
        player: Any,
        current_date: datetime,
        just_played: bool = False,
    ) -> None:
        """Cập nhật churn_status + lifecycle stage cho player.

        Args:
            player: Player dataclass (cần install_date, churn_segment,
                    churn_status, last_session_date).
            current_date: datetime UTC hiện tại.
            just_played: True nếu player vừa chơi session hôm nay.
        """
        # Update last_session_date nếu vừa chơi
        if just_played:
            player.last_session_date = current_date

        # Tính player_age
        player_age = self.compute_player_age_days(player, current_date)

        # Xác định churn_status dựa trên last_session_date
        old_status = player.churn_status
        new_status = self._compute_churn_status(player, current_date)
        player.churn_status = new_status

        # Lifecycle stage progression (chỉ advance, không regress)
        if hasattr(player, "lifecycle_stage"):
            # Nếu có lifecycle_stage field
            current_stage = player.lifecycle_stage
            new_stage = self._advance_stage(
                current_stage, player_age,
            )
            player.lifecycle_stage = new_stage

    def compute_player_age_days(
        self,
        player: Any,
        current_date: datetime,
    ) -> int:
        """Tính tuổi player theo ngày (≥0).

        Args:
            player: Player (cần install_date).
            current_date: datetime UTC.

        Returns:
            Số ngày từ install_date đến current_date (≥0).
        """
        install = player.install_date
        # Đảm bảo cả 2 là timezone-aware UTC
        if install.tzinfo is None:
            install = install.replace(tzinfo=timezone.utc)
        if current_date.tzinfo is None:
            current_date = current_date.replace(tzinfo=timezone.utc)
        delta = current_date - install
        return max(0, delta.days)

    def is_active_today(
        self,
        player: Any,
        current_date: datetime,
        rng: random.Random | None = None,
    ) -> bool:
        """Kiểm tra player có active trong ngày này không.

        Quy tắc:
            - Player đã churned → KHÔNG active.
            - Player trong retention window (D1-D30) → active theo curve.
            - Player dormant → KHÔNG active.
            - Player active hôm qua + curve vẫn allow → active với
              probability = curve.rate_at_day(age) + daily_active_probability.

        Args:
            player: Player (cần churn_segment, churn_status, last_session_date).
            current_date: datetime UTC.
            rng: Random override (cho test).

        Returns:
            True nếu player nên chơi hôm nay.
        """
        # Churned → không bao giờ active
        if player.churn_status == CHURN_STATUS_CHURNED:
            return False

        _rng = rng or self._rng

        player_age = self.compute_player_age_days(player, current_date)

        # Lấy retention curve
        curve = RetentionCurve.for_segment(player.churn_segment)

        # Trong retention window (D1-D30) → active theo curve
        if player_age <= 30:
            # rate_at_day = % player còn active tại age này
            target_rate = curve.rate_at_day(player_age)
            return _rng.random() < target_rate

        # Sau D30 → daily_active_probability cho churn_segment
        daily_prob = self._daily_active.get(player.churn_segment, 0.0)
        return _rng.random() < daily_prob

    def transition_stage(
        self,
        player: Any,
        new_stage: str,
    ) -> None:
        """Force stage transition (cho logic bên ngoài như first_session).

        Args:
            player: Player.
            new_stage: 1 trong LIFECYCLE_STAGES.
        """
        if new_stage not in LIFECYCLE_STAGES:
            raise ValueError(
                f"new_stage '{new_stage}' không hợp lệ. "
                f"Có: {LIFECYCLE_STAGES}"
            )

        if not hasattr(player, "lifecycle_stage"):
            player.lifecycle_stage = "acquisition"

        current = player.lifecycle_stage
        if new_stage not in LIFECYCLE_TRANSITIONS.get(current, ()):
            # Log warning nhưng vẫn cho phép
            pass
        player.lifecycle_stage = new_stage

    # ------------------------------------------------------------------
    # Cohort analysis
    # ------------------------------------------------------------------
    def build_cohorts(
        self,
        players: list[Any],
        simulation_date: datetime | None = None,
    ) -> dict[str, RetentionCohort]:
        """Build retention cohorts từ list player.

        Args:
            players: list Player.
            simulation_date: datetime UTC. None = dùng time_source.

        Returns:
            dict cohort_key → RetentionCohort.
        """
        if simulation_date is None:
            simulation_date = self._time_source()

        cohorts: dict[tuple[str, str], list[str]] = defaultdict(list)

        for player in players:
            install_date = self._format_date(player.install_date)
            segment = player.churn_segment
            cohorts[(install_date, segment)].append(player.user_id)

        result: dict[str, RetentionCohort] = {}
        for (install_date, segment), uids in cohorts.items():
            cohort = RetentionCohort(
                install_date=install_date,
                churn_segment=segment,
                user_ids=uids,
            )
            result[cohort.cohort_key] = cohort
        return result

    def compute_retention_curve(
        self,
        cohort: RetentionCohort,
        checkpoints: Iterable[int] | None = None,
    ) -> dict[int, float]:
        """Tính retention curve thực tế từ config (theo cohort_segment).

        Args:
            cohort: RetentionCohort (chỉ cần churn_segment).
            checkpoints: list day checkpoints. None = RETENTION_CHECKPOINTS.

        Returns:
            dict {day: retention_rate}.
        """
        if checkpoints is None:
            checkpoints = RETENTION_CHECKPOINTS
        curve = RetentionCurve.for_segment(cohort.churn_segment)
        return {
            day: curve.rate_at_day(day) for day in checkpoints
        }

    def get_stats(
        self,
        players: list[Any],
    ) -> RetentionStats:
        """Tính retention stats cho toàn bộ player.

        Args:
            players: list Player.

        Returns:
            RetentionStats.
        """
        stats = RetentionStats()
        stats.total_players = len(players)
        stats.segment_counts = defaultdict(int)

        for p in players:
            if p.churn_status == CHURN_STATUS_ACTIVE:
                stats.active_players += 1
            elif p.churn_status == CHURN_STATUS_DORMANT:
                stats.dormant_players += 1
            elif p.churn_status == CHURN_STATUS_CHURNED:
                stats.churned_players += 1
            stats.segment_counts[p.churn_segment] += 1

        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_churn_status(
        self,
        player: Any,
        current_date: datetime,
    ) -> str:
        """Xác định churn_status dựa trên last_session_date.

        Rules:
            - Chưa từng chơi → ACTIVE (vừa install).
            - Vừa chơi hôm nay → ACTIVE.
            - Không chơi < dormant_after_days → ACTIVE.
            - Không chơi >= dormant_after_days → DORMANT.
            - Không chơi >= churned_after_days → CHURNED.
        """
        if player.last_session_date is None:
            return CHURN_STATUS_ACTIVE

        last = player.last_session_date
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if current_date.tzinfo is None:
            current_date = current_date.replace(tzinfo=timezone.utc)

        days_since_last = (current_date - last).days

        if days_since_last < self._dormant_after:
            return CHURN_STATUS_ACTIVE
        if days_since_last < self._churned_after:
            return CHURN_STATUS_DORMANT
        return CHURN_STATUS_CHURNED

    def _advance_stage(
        self,
        current_stage: str,
        player_age_days: int,
    ) -> str:
        """Advance lifecycle stage dựa trên player_age.

        Chỉ forward transition (không regress). Có thể advance nhiều
        stages trong 1 lần (vd: 0 → engagement nếu player_age=7).
        """
        if current_stage not in LIFECYCLE_STAGES:
            return "engagement"

        stage = current_stage
        # Loop để advance nhiều stages
        while True:
            next_stage = self._next_stage_for_age(stage, player_age_days)
            if next_stage is None or next_stage == stage:
                break
            stage = next_stage
        return stage

    def _next_stage_for_age(
        self,
        current_stage: str,
        player_age_days: int,
    ) -> str | None:
        """Xác định stage tiếp theo (None nếu không advance)."""
        if current_stage in ("acquisition", "install", "first_session"):
            if player_age_days >= ACTIVATION_AGE_DAYS:
                return "activation"

        if current_stage == "activation":
            if player_age_days >= ENGAGEMENT_AGE_DAYS:
                return "engagement"

        if current_stage == "engagement":
            if player_age_days >= RETENTION_THRESHOLD_AGE_DAYS:
                # 30% sẽ monetization, 70% retention
                if self._rng.random() < MONETIZATION_PROBABILITY:
                    return "monetization"
                return "retention"

        if current_stage == "monetization":
            if player_age_days >= RETENTION_THRESHOLD_AGE_DAYS:
                return "retention"

        return None

    @staticmethod
    def _format_date(dt: datetime) -> str:
        """Format datetime → ISO date string."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime(COHORT_DATE_FORMAT)


# =========================================================================
# Convenience helpers
# =========================================================================

def create_retention_simulator(
    rng_seed: int | None = None,
) -> RetentionSimulator:
    """Factory: tạo RetentionSimulator với optional deterministic seed.

    Args:
        rng_seed: seed cho random. None = non-deterministic.

    Returns:
        RetentionSimulator.
    """
    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    return RetentionSimulator(rng=rng)


__all__ = [
    "CHURN_STATUS_ACTIVE",
    "CHURN_STATUS_CHURNED",
    "CHURN_STATUS_DORMANT",
    "DailyRetention",
    "RetentionCohort",
    "RetentionCurve",
    "RetentionSimulator",
    "RetentionStats",
    "create_retention_simulator",
]
