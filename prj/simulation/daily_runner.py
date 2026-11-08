"""Daily Simulation Runner – điều phối 1 ngày simulation.

Theo BƯỚC 25 INSTRUCTION.md:

    START
      ↓
    Load configuration
      ↓
    Load GameAnalytics credentials (.env)
      ↓
    Initialize database (SQLite)
      ↓
    Create / load players
      ↓
    For each simulation day:
      │
      ├── Determine active players (retention model)
      │
      ├── Create sessions
      │   ├── Create user event (session start)
      │
      ├── Generate gameplay (progression + design)
      ├── Generate ads (impression + rewarded)
      ├── Generate resources (source/sink)
      ├── Generate IAP (business, theo payer segment)
      ├── Generate rare errors
      ├── Generate session_end
      │
      ├── Validate
      ├── Write raw
      ├── Build batches (< 1 MB gzip)
      └── POST GameAnalytics
      ↓
    Next simulation day
      ↓
    FINISH

    - Dùng stream/batch chứ không load toàn bộ events vào RAM.
    - Mỗi batch xong → ghi raw + gửi API → clear buffer → lặp tiếp.

Module này cung cấp:

    - ``SessionContext``: state tạm cho 1 session đang chạy
    - ``DailyRunner``: chạy 1 ngày simulation
    - ``SimulationRunner``: chạy N ngày simulation
    - ``BatchSink``: callback nhận batch đã gom xong (ghi raw + POST GA)
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from gameanalytics.batcher import Batcher, Batch
from gameanalytics.retry import (
    RetryableGameAnalyticsClient,
    RetryPolicy,
    create_retry_client,
)
from gameanalytics.validator import EventValidator, ValidationResult
from generators import (
    build_rewarded_resource_tx,
    generate_ad_event,
    generate_business_event,
    generate_design_event,
    generate_error_event,
    generate_progression_event,
    generate_resource_event,
    generate_session_end_event,
    generate_user_event,
)
from models import Player, Session, generate_session
from simulation.ad import AdSimulator
from simulation.design import DesignSimulator
from simulation.economy import EconomySimulator
from simulation.error import ErrorSimulator
from simulation.monetization import MonetizationSimulator
from simulation.progression import ProgressionSimulator
from simulation.retention import RetentionSimulator
from storage.raw_writer import RawDataWriter
from storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)


# =========================================================================
# SessionContext
# =========================================================================

@dataclass
class SessionContext:
    """Context tạm cho 1 session đang chạy.

    Dùng để pass giữa các generators mà không giữ all-in-RAM.

    Attributes:
        player: Player model.
        session: Session model vừa tạo.
        session_events: list events đã sinh trong session (cleared khi batch).
        events_count_per_category: counter để tracking.
    """

    player: Player
    session: Session
    session_events: list[dict[str, Any]] = field(default_factory=list)
    events_count_per_category: dict[str, int] = field(default_factory=dict)

    def add_event(self, event: dict[str, Any]) -> None:
        """Thêm 1 event vào context."""
        self.session_events.append(event)
        cat = event.get("category", "unknown")
        self.events_count_per_category[cat] = (
            self.events_count_per_category.get(cat, 0) + 1
        )

    def drain_events(self) -> list[dict[str, Any]]:
        """Lấy hết events và clear buffer."""
        events = list(self.session_events)
        self.session_events.clear()
        return events


# =========================================================================
# DailyRunner
# =========================================================================

class DailyRunner:
    """Chạy simulation cho 1 ngày.

    Quy trình:
        1. Determine active players (RetentionSimulator.is_active_today)
        2. For each active player → chạy 1 session
        3. Sinh events: user → gameplay loop → session_end
        4. Validate → Batch → Write raw → POST → Clear buffer
    """

    def __init__(
        self,
        # Core dependencies
        rng: random.Random | None = None,
        retention_simulator: RetentionSimulator | None = None,
        # Generators (configurable cho test)
        progression_simulator: ProgressionSimulator | None = None,
        economy_simulator: EconomySimulator | None = None,
        monetization_simulator: MonetizationSimulator | None = None,
        ad_simulator: AdSimulator | None = None,
        design_simulator: DesignSimulator | None = None,
        error_simulator: ErrorSimulator | None = None,
        # Gameplay behavior
        design_event_count_range: tuple[int, int] = (1, 4),
        progression_attempts_per_session: int = 2,
        ad_count_range: tuple[int, int] = (1, 3),
        resource_count_range: tuple[int, int] = (2, 6),
        # Pipeline
        validator: EventValidator | None = None,
        batcher: Batcher | None = None,
        sink: Callable[[Batch], None] | None = None,
    ) -> None:
        """Khởi tạo DailyRunner.

        Args:
            rng: Random instance.
            retention_simulator: RetentionSimulator instance.
            progression_simulator: ProgressionSimulator instance.
            economy_simulator: EconomySimulator instance.
            monetization_simulator: MonetizationSimulator instance.
            ad_simulator: AdSimulator instance.
            design_simulator: DesignSimulator instance.
            error_simulator: ErrorSimulator instance.
            design_event_count_range: (min, max) design event / session.
            progression_attempts_per_session: số attempt progression / session.
            ad_count_range: (min, max) ad / session.
            resource_count_range: (min, max) resource event / session.
            validator: EventValidator (None = skip validation).
            batcher: Batcher (None = tạo mới).
            sink: callback khi batch flush (write raw + POST).
        """
        self._rng = rng or random.Random()

        self.retention_simulator = retention_simulator or RetentionSimulator(
            rng=self._rng,
        )
        # ProgressionSimulator là per-session scope (cần player + session_start_ts),
        # sẽ tạo trong _run_player_session().
        self.progression_simulator = progression_simulator
        self.economy_simulator = economy_simulator or EconomySimulator(rng=self._rng)
        self.monetization_simulator = (
            monetization_simulator or MonetizationSimulator(rng=self._rng)
        )
        self.ad_simulator = ad_simulator or AdSimulator(rng=self._rng)
        self.design_simulator = design_simulator or DesignSimulator(rng=self._rng)
        self.error_simulator = error_simulator or ErrorSimulator(rng=self._rng)

        self.design_event_count_range = design_event_count_range
        self.progression_attempts_per_session = progression_attempts_per_session
        self.ad_count_range = ad_count_range
        self.resource_count_range = resource_count_range

        self.validator = validator or EventValidator()
        # Batcher dùng callback on_flush thay vì sink ngoài
        # (sẽ flush ngay khi đầy batch, không load all-in-RAM)
        if batcher is not None:
            self.batcher = batcher
        else:
            def _on_flush(batch: Batch) -> None:
                self._on_batch_flushed(batch)
            self.batcher = Batcher(on_flush=_on_flush)
        self.sink = sink
        # Persistence callback: invoked sau khi session hoàn tất, dùng để
        # ghi Player state + session record xuống SQLite.
        # Signature: callable(Player, Session) -> None
        self.persistence_callback: Callable[[Player, Session], None] | None = None

        # Daily stats (cleared mỗi run_day)
        self._daily_stats = {
            "sessions_active": 0,
            "events_generated": 0,
            "revenue_minor": 0,
            "paying_players": set(),
            "by_category": Counter(),
            "by_source": Counter(),
            "by_country": Counter(),
            "errors": 0,
            "validations_failed": 0,
            "batches_flushed": 0,
            "wall_clock_sec": 0.0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_day(
        self,
        players: list[Player],
        current_date: datetime,
        force_active_user_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Chạy simulation cho 1 ngày.

        Args:
            players: list Player trong DB.
            current_date: datetime UTC ngày đang chạy.
            force_active_user_ids: set user_id luôn active (vd: new install).
                                  None = chỉ dùng retention.

        Returns:
            dict daily stats.
        """
        start_time = time.time()
        self._reset_daily_stats()

        # Determine active players
        active_players = self._filter_active_players(
            players, current_date, force_active_user_ids,
        )

        logger.info(
            "DailyRunner: date=%s, players=%d, active=%d",
            current_date.strftime("%Y-%m-%d"),
            len(players),
            len(active_players),
        )

        # Run sessions for each active player
        for player in active_players:
            try:
                ctx = self._run_player_session(player, current_date)
                # Update retention state
                self.retention_simulator.update_player(
                    player, current_date, just_played=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Error in player session (user_id=%s): %s",
                    getattr(player, "user_id", "?"), e,
                )
                self._daily_stats["errors"] += 1

        # Flush remaining events trong batcher
        self._flush_batcher()

        # Stats summary
        self._daily_stats["wall_clock_sec"] = time.time() - start_time
        return dict(self._daily_stats)

    def run_day_streaming(
        self,
        players: list[Player],
        current_date: datetime,
    ) -> Iterable[Batch]:
        """Generator version: yield Batch ngay khi flush.

        Dùng cho streaming pipeline không cần collect tất cả vào RAM.
        """
        self._reset_daily_stats()
        active_players = self._filter_active_players(players, current_date)
        for player in active_players:
            try:
                self._run_player_session(player, current_date)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error: %s", e)
                self._daily_stats["errors"] += 1

        # Yield các batch còn lại
        while True:
            batch = self.batcher.flush()
            if batch is None:
                break
            self._daily_stats["batches_flushed"] += 1
            yield batch

    # ------------------------------------------------------------------
    # Session logic
    # ------------------------------------------------------------------
    def _run_player_session(
        self,
        player: Player,
        current_date: datetime,
    ) -> SessionContext:
        """Chạy 1 session đầy đủ cho player.

        FIX TIMESTAMP BUG (29/09/2026):
            Trước đây gọi ``generate_session(player)`` không truyền
            ``session_start_ts``, nên Session bị fallback về ``datetime.now()``
            và mọi event ``client_ts`` cluster về thời điểm ingest thay vì
            theo từng ngày trong window simulation.

            Fix: tính ``session_start_ts`` từ ``current_date`` (UTC 00:00) +
            random hour/minute/second, truyền vào ``generate_session``.
        """
        # 1. Generate session
        # Pick a random time-of-day trong ngày current_date.
        # Tránh giây quá trễ (vd 23:59:30) để session không tràn sang ngày sau.
        rng = self._rng
        hour = rng.randint(8, 22)        # 8h sáng → 22h tối (giờ game thực tế)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        session_start_ts = int(
            current_date.replace(hour=hour, minute=minute, second=second,
                                 microsecond=0).timestamp()
        )

        session = generate_session(
            player,
            session_start_ts=session_start_ts,
            rng=rng,
        )

        ctx = SessionContext(player=player, session=session)

        # 2. user_event (session start)
        user_event = generate_user_event(player, session)
        ctx.add_event(user_event)

        # 3. Run gameplay loop (progression → design → ad → resource → IAP → error)
        self._run_gameplay_loop(ctx, current_date)

        # 4. session_end
        # FIX TIMESTAMP BUG #2 (29/09/2026):
        #     Trước đây gọi ``session.close()`` không truyền end_ts, nên
        #     Session.close() fallback về ``datetime.now()`` và duration_seconds
        #     vượt quá 48h (giới hạn GA), gây raise ValueError trong
        #     generate_session_end_event().
        #
        #     Fix: tính session_end_ts = session_start_ts + random duration
        #     trong khoảng duration_range của session, đảm bảo end_ts nằm
        #     trong cùng ngày simulation (không tràn qua ngày khác).
        # Lấy duration range từ session_durations config (segment-specific)
        from config.game_config import SESSION_DURATION_RANGE_SECONDS
        engagement_seg = getattr(player, "engagement_segment", "casual")
        duration_range = SESSION_DURATION_RANGE_SECONDS.get(
            engagement_seg,
            SESSION_DURATION_RANGE_SECONDS.get("casual", (60, 1800)),
        )
        # Clamp: đảm bảo session không kéo dài quá 23:59:59 của current_date
        max_end_ts = int(
            current_date.replace(hour=23, minute=59, second=59).timestamp()
        )
        max_duration = max(1, max_end_ts - session_start_ts)
        min_dur = min(duration_range[0], max_duration)
        max_dur = min(duration_range[1], max_duration)
        if min_dur > max_dur:
            min_dur = max_dur
        duration_seconds = rng.randint(min_dur, max_dur)
        end_ts = session_start_ts + duration_seconds

        session.close(end_ts=end_ts)  # sets session_end_ts + duration
        session_end_event = generate_session_end_event(player, session)
        ctx.add_event(session_end_event)

        # 5. Validate & batch & flush
        self._validate_and_batch(ctx)

        # 6. Update player state (in-memory)
        player.session_count = session.session_num
        player.last_session_date = current_date

        # 7. Persist xuống SQLite (nếu callback được wire từ SimulationRunner)
        if self.persistence_callback is not None:
            try:
                self.persistence_callback(player, session)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Persistence callback failed for user_id=%s: %s",
                    player.user_id, e,
                )
                self._daily_stats["errors"] += 1

        self._daily_stats["sessions_active"] += 1
        return ctx

    def _run_gameplay_loop(
        self,
        ctx: SessionContext,
        current_date: datetime,
    ) -> None:
        """Chạy gameplay loop (progression + design + ad + resource + IAP)."""
        player = ctx.player
        session = ctx.session

        # Tạo ProgressionSimulator per-session (cần player + session_start_ts)
        progression = self.progression_simulator or ProgressionSimulator(
            player=player,
            session_start_ts=session.session_start_ts,
            rng=self._rng,
        )

        # 3a. Progression attempts (drive state machine với client_ts)
        base_ts = session.session_start_ts
        # Lấy tối đa progression_attempts_per_session actions
        for attempt_i in range(self.progression_attempts_per_session):
            # Mỗi attempt cách nhau ~30s gameplay
            client_ts = base_ts + (attempt_i + 1) * 30
            prog_action = progression.next_action(client_ts=client_ts)
            if prog_action is None:
                break
            prog_event = generate_progression_event(
                player, session, prog_action,
            )
            ctx.add_event(prog_event)

        # 3b. Design events (try_events_for_session)
        decisions = self.design_simulator.try_events_for_session(
            player=player,
            client_start_ts=session.session_start_ts,
            n_max=max(self.design_event_count_range),
            ts_step_seconds=60,
        )
        for design_decision in decisions[: max(self.design_event_count_range)]:
            design_event = generate_design_event(
                player, session, design_decision,
            )
            ctx.add_event(design_event)

        # 3c. Ad impressions (try_session_ads)
        n_ads = self._rng.randint(*self.ad_count_range)
        ad_decisions = self.ad_simulator.try_session_ads(
            player=player,
            client_start_ts=session.session_start_ts,
            n_max=n_ads,
        )
        for ad_decision in ad_decisions:
            ad_event = generate_ad_event(player, session, ad_decision)
            ctx.add_event(ad_event)
            # Rewarded ad → bonus resource
            if ad_decision.placement == "rewarded":
                tx = build_rewarded_resource_tx(
                    player, session, ad_decision,
                    rng=self._rng,
                )
                if tx is not None:
                    resource_event = generate_resource_event(
                        player, session, tx,
                    )
                    ctx.add_event(resource_event)

        # 3d. Resource transactions (Source/Sink theo gameplay)
        n_resources = self._rng.randint(*self.resource_count_range)
        base_ts = session.session_start_ts
        for i in range(n_resources):
            # Pick source hay sink ngẫu nhiên
            if self._rng.random() < 0.6:
                # Source: player nhận resource
                tx = self.economy_simulator.random_source(
                    player, base_ts + i * 30,
                )
            else:
                # Sink: player tiêu resource
                tx = self.economy_simulator.random_sink(
                    player, base_ts + i * 30,
                    allow_overdraft=True,
                )
            if tx is not None:
                resource_event = generate_resource_event(
                    player, session, tx,
                )
                ctx.add_event(resource_event)

        # 3e. IAP (chỉ từ payer – simulator đã check internally)
        purchase = self.monetization_simulator.try_purchase(
            player, session.session_start_ts,
        )
        if purchase is not None:
            business_event = generate_business_event(
                player, session, purchase,
            )
            ctx.add_event(business_event)

        # 3f. Rare error event (cap MAX_ERROR_EVENTS_PER_SESSION internally)
        error_decision = self.error_simulator.try_event(
            session.session_start_ts,
        )
        if error_decision is not None:
            error_event = generate_error_event(
                player, session, error_decision,
            )
            ctx.add_event(error_event)

    # ------------------------------------------------------------------
    # Validate / Batch / Sink
    # ------------------------------------------------------------------
    def _validate_and_batch(self, ctx: SessionContext) -> None:
        """Validate từng event, gom vào batcher, gọi sink khi flush.

        Side-effects trên daily_stats:
            - by_category: tăng theo event category.
            - by_source: tăng theo acquisition_source (player-level, snapshot).
            - by_country: tăng theo player.country (snapshot tại session).
              GameAnalytics schema v2 không có field ``country`` annotation,
              nên phải dùng metadata của player làm nguồn country.
            - revenue_minor: cộng dồn amount (minor unit) cho business event.
            - paying_players: thêm user_id vào set nếu có business event.
        """
        events = ctx.drain_events()
        player_country = getattr(ctx.player, "country", None) or "unknown"
        acquisition = getattr(ctx.player, "acquisition_source", None) or "unknown"

        for event in events:
            # Validate (schema layer)
            schema_result = self.validator.validate_schema(event)
            if not schema_result:
                self._daily_stats["validations_failed"] += 1
                logger.debug("Validation failed: %s", schema_result.errors)
                continue

            # Track stats
            cat = event.get("category", "unknown")
            self._daily_stats["by_category"][cat] += 1
            self._daily_stats["by_source"][acquisition] += 1
            self._daily_stats["by_country"][player_country] += 1
            self._daily_stats["events_generated"] += 1

            # Revenue tracking: business event có amount (minor unit)
            if cat == "business":
                amount = int(event.get("amount", 0) or 0)
                if amount > 0:
                    self._daily_stats["revenue_minor"] += amount
                    self._daily_stats["paying_players"].add(
                        event.get("user_id", "?")
                    )

            # Add to batcher (returns Batch | None khi flush)
            flushed = self.batcher.add_event(event)
            if flushed is not None:
                self._on_batch_flushed(flushed)

    def _on_batch_flushed(self, batch: Batch) -> None:
        """Callback khi batch đầy → ghi raw + gửi API."""
        self._daily_stats["batches_flushed"] += 1
        if self.sink is not None:
            try:
                self.sink(batch)
            except Exception as e:  # noqa: BLE001
                logger.error("Sink failed for batch: %s", e)

    def _flush_batcher(self) -> None:
        """Flush tất cả batch còn lại trong buffer."""
        while True:
            batch = self.batcher.flush()
            if batch is None:
                break
            self._on_batch_flushed(batch)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _filter_active_players(
        self,
        players: list[Player],
        current_date: datetime,
        force_active_user_ids: set[str] | None = None,
    ) -> list[Player]:
        """Determine active players hôm nay.

        Logic:
            - New install (install_date == current_date): active.
            - User trong force_active_user_ids: active.
            - is_active_today theo RetentionSimulator: active.
        """
        active: list[Player] = []
        for p in players:
            if force_active_user_ids and p.user_id in force_active_user_ids:
                active.append(p)
                continue
            if self.retention_simulator.is_active_today(
                p, current_date, rng=self._rng,
            ):
                active.append(p)
        return active

    @staticmethod
    def _country_from_event(event: dict[str, Any]) -> str:
        """Deprecated – country không nằm trong GA annotation schema v2.

        Giữ lại cho backward-compat, nhưng code mới dùng
        ``ctx.player.country`` trực tiếp.
        """
        return event.get("country", "unknown")

    def _reset_daily_stats(self) -> None:
        self._daily_stats = {
            "sessions_active": 0,
            "events_generated": 0,
            "revenue_minor": 0,
            "paying_players": set(),
            "by_category": Counter(),
            "by_source": Counter(),
            "by_country": Counter(),
            "errors": 0,
            "validations_failed": 0,
            "batches_flushed": 0,
            "wall_clock_sec": 0.0,
        }


# =========================================================================
# SimulationRunner – chạy N ngày
# =========================================================================

class SimulationRunner:
    """Chạy N ngày simulation tuần tự.

    Usage::

        runner = SimulationRunner(
            daily_runner=...,
            storage=storage,
            raw_writer=raw_writer,
            client=client,
        )
        report = runner.run(start_date, n_days, players)
    """

    def __init__(
        self,
        daily_runner: DailyRunner,
        storage: SQLiteStorage | None = None,
        raw_writer: RawDataWriter | None = None,
        client: RetryableGameAnalyticsClient | None = None,
        # Optional: persistence callback sau mỗi session
        on_session_complete: Callable[[Player, Session], None] | None = None,
    ) -> None:
        """Khởi tạo SimulationRunner."""
        self.daily_runner = daily_runner
        self.storage = storage
        self.raw_writer = raw_writer
        self.client = client
        self.on_session_complete = on_session_complete

        # Wire persistence callback xuống DailyRunner nếu có storage.
        # Mục đích: sau mỗi session, ghi lại Player state (current_level,
        # transaction_num, lifetime_revenue, ...) và session record xuống
        # SQLite. Nếu thiếu wiring này, DB sẽ trống rỗng sau simulation.
        if storage is not None and daily_runner.persistence_callback is None:
            daily_runner.persistence_callback = self._persist_session

        # Buffer sessions của 1 ngày, flush bằng 1 transaction cuối ngày.
        # Khởi tạo rỗng ở đây để _persist_session dùng được.
        self._session_buffer: list[tuple[Player, "Session"]] = []

        # Aggregate stats
        self._aggregate_stats = {
            "total_days": 0,
            "total_sessions": 0,
            "total_events": 0,
            "total_batches": 0,
            "total_revenue_minor": 0,
            "total_paying_players": set(),
            "by_category": Counter(),
            "by_country": Counter(),
            "total_errors": 0,
            "wall_clock_sec": 0.0,
        }

    def _persist_session(self, player: Player, session: Session) -> None:
        """Buffer session + player state trong RAM.

        Được DailyRunner gọi sau khi session đóng. Toàn bộ session của 1
        ngày được flush xuống SQLite trong 1 transaction duy nhất nhờ
        ``flush_day_sessions`` (xem cuối run_day).

        Lý do batch thay vì per-session: với 5000 players × 30 days,
        per-session open/close connection + commit tốn ~30% wall clock.
        Batch giảm xuống ~1% (1 transaction cuối ngày).

        Nếu storage là None → no-op.
        """
        if self.storage is None:
            return
        self._session_buffer.append((player, session))

    def _flush_session_buffer(self) -> int:
        """Flush toàn bộ buffered sessions xuống SQLite trong 1 transaction.

        Returns:
            Số session đã flush. 0 nếu buffer rỗng hoặc storage None.
        """
        if self.storage is None or not self._session_buffer:
            return 0

        count = len(self._session_buffer)
        # bulk_persist_sessions tự wrap trong transaction, commit 1 lần.
        # Bọc try/except để không chặn simulation nếu 1 row lỗi.
        try:
            self.storage.bulk_persist_sessions(self._session_buffer)
        except Exception:  # noqa: BLE001
            # Fallback: per-session insert để không mất data hoàn toàn.
            for player, session in self._session_buffer:
                try:
                    with self.storage as s:
                        s.update_player_state(player)
                        try:
                            s.upsert_economy(player)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            s.upsert_progression(
                                user_id=player.user_id,
                                current_level=player.current_level,
                                current_world=player.current_world,
                                current_attempt_num=player.current_attempt_num,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            s.save_session(
                                session_id=session.session_id,
                                user_id=player.user_id,
                                session_num=session.session_num,
                                session_start_ts=session.session_start_ts,
                                session_end_ts=session.session_end_ts,
                                duration_seconds=session.duration_seconds,
                                connection_type=session.connection_type,
                                engagement_segment=player.engagement_segment,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._session_buffer.clear()
        return count

        # 5. Optional custom callback (ngoài context để tránh giữ connection)
        if self.on_session_complete is not None:
            try:
                self.on_session_complete(player, session)
            except Exception:  # noqa: BLE001
                pass

    def run(
        self,
        players: list[Player],
        start_date: datetime,
        n_days: int,
        new_install_user_ids: dict[int, set[str]] | None = None,
        on_day_complete: Callable[[datetime, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Chạy N ngày simulation.

        Args:
            players: list Player (đã load từ DB hoặc generated mới).
            start_date: datetime UTC bắt đầu.
            n_days: số ngày chạy.
            new_install_user_ids: dict {day_offset: set user_ids mới install}.
                Ngày có user mới sẽ force các user đó active.
            on_day_complete: optional callback ``(current_date, daily_stats)``
                được gọi SAU KHI aggregate daily_stats lên total. Dùng để
                ReportBuilder lưu từng DailyReport trong khi SimulationRunner
                vẫn là single source of truth cho aggregate totals.

        Returns:
            dict aggregate stats (đã bao gồm total_paying_players count int).
        """
        start_time = time.time()
        self._reset_aggregate_stats()
        self._aggregate_stats["total_days"] = n_days

        for day_offset in range(n_days):
            current_date = start_date + timedelta(days=day_offset)
            force_active = None
            if new_install_user_ids:
                force_active = new_install_user_ids.get(day_offset, set())

            daily_stats = self.daily_runner.run_day(
                players, current_date, force_active_user_ids=force_active,
            )

            # Aggregate
            self._aggregate_stats["total_sessions"] += (
                daily_stats["sessions_active"]
            )
            self._aggregate_stats["total_events"] += (
                daily_stats["events_generated"]
            )
            self._aggregate_stats["total_batches"] += (
                daily_stats["batches_flushed"]
            )
            self._aggregate_stats["total_errors"] += daily_stats["errors"]
            # Revenue aggregate (minor unit). paying_players là set nên
            # union để đếm distinct paying users.
            self._aggregate_stats["total_revenue_minor"] += (
                daily_stats.get("revenue_minor", 0)
            )
            for uid in daily_stats.get("paying_players", set()):
                self._aggregate_stats["total_paying_players"].add(uid)
            for cat, cnt in daily_stats["by_category"].items():
                self._aggregate_stats["by_category"][cat] += cnt
            for country, cnt in daily_stats["by_country"].items():
                self._aggregate_stats["by_country"][country] += cnt

            # Notify caller (ReportBuilder) sau khi aggregate xong
            if on_day_complete is not None:
                try:
                    on_day_complete(current_date, daily_stats)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "on_day_complete callback failed for %s: %s",
                        current_date.strftime("%Y-%m-%d"), e,
                    )

            # Flush toàn bộ sessions của ngày này xuống SQLite trong
            # 1 transaction duy nhất. Bulk path thay vì per-session
            # open/close connection → giảm overhead ~50-100x.
            flushed = self._flush_session_buffer()
            if flushed > 0:
                logger.debug(
                    "Flushed %d sessions for %s",
                    flushed, current_date.strftime("%Y-%m-%d"),
                )

        self._aggregate_stats["wall_clock_sec"] = time.time() - start_time
        # Trả về JSON-friendly dict: set → int, Counter → dict.
        # Caller (ReportBuilder hoặc main) nhận được:
        #   - total_paying_players: int (count distinct)
        #   - by_category, by_country: dict (chứ không phải Counter)
        # Điều này đảm bảo SimulationReport.by_category được type-hint
        # đúng là dict[str, int] thay vì Counter.
        result = {
            "total_days": self._aggregate_stats["total_days"],
            "total_sessions": self._aggregate_stats["total_sessions"],
            "total_events": self._aggregate_stats["total_events"],
            "total_batches": self._aggregate_stats["total_batches"],
            "total_revenue_minor": self._aggregate_stats["total_revenue_minor"],
            "total_paying_players": len(
                self._aggregate_stats["total_paying_players"]
            ),
            "by_category": dict(self._aggregate_stats["by_category"]),
            "by_country": dict(self._aggregate_stats["by_country"]),
            "total_errors": self._aggregate_stats["total_errors"],
            "wall_clock_sec": self._aggregate_stats["wall_clock_sec"],
        }
        return result

    def get_stats(self) -> dict[str, Any]:
        result = {
            "total_days": self._aggregate_stats["total_days"],
            "total_sessions": self._aggregate_stats["total_sessions"],
            "total_events": self._aggregate_stats["total_events"],
            "total_batches": self._aggregate_stats["total_batches"],
            "total_revenue_minor": self._aggregate_stats["total_revenue_minor"],
            "total_paying_players": len(
                self._aggregate_stats["total_paying_players"]
            ),
            "by_category": dict(self._aggregate_stats["by_category"]),
            "by_country": dict(self._aggregate_stats["by_country"]),
            "total_errors": self._aggregate_stats["total_errors"],
            "wall_clock_sec": self._aggregate_stats["wall_clock_sec"],
        }
        return result

    def _reset_aggregate_stats(self) -> None:
        self._aggregate_stats = {
            "total_days": 0,
            "total_sessions": 0,
            "total_events": 0,
            "total_batches": 0,
            "total_revenue_minor": 0,
            "total_paying_players": set(),
            "by_category": Counter(),
            "by_country": Counter(),
            "total_errors": 0,
            "wall_clock_sec": 0.0,
        }


# =========================================================================
# Convenience helpers
# =========================================================================

def create_batch_sink(
    raw_writer: RawDataWriter | None = None,
    client: RetryableGameAnalyticsClient | None = None,
    dry_run: bool = False,
) -> Callable[[Batch], None]:
    """Tạo batch sink callback kết hợp RawDataWriter + GA Client.

    Args:
        raw_writer: ghi raw data trước khi POST.
        client: GameAnalytics client (RetryableGameAnalyticsClient).
        dry_run: True = skip POST (chỉ ghi raw).

    Returns:
        Callable nhận Batch.
    """
    def sink(batch: Batch) -> None:
        # 1. Ghi raw trước (cần batch_id)
        if raw_writer is not None:
            import uuid as _uuid
            batch_id = f"batch_{_uuid.uuid4().hex[:8]}"
            try:
                raw_writer.write_batch(batch_id=batch_id, events=batch.events)
            except Exception as e:  # noqa: BLE001
                logger.error("RawWriter failed: %s", e)

        # 2. Gửi tới GameAnalytics
        if not dry_run and client is not None:
            try:
                client.send_events(batch.events)
            except Exception as e:  # noqa: BLE001
                logger.error("Client send failed: %s", e)
    return sink


__all__ = [
    "Batch",
    "DailyRunner",
    "SessionContext",
    "SimulationRunner",
    "create_batch_sink",
]
