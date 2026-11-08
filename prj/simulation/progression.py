"""Progression Simulator – quản lý state & quyết định logic progression.

Theo BƯỚC 9 INSTRUCTION.md:

Mô phỏng Level/Stage/Chapter/Quest với 3 trạng thái ``Start``, ``Fail``,
``Complete``.

Hierarchy cố định: ``World → Level`` (ví dụ ``World1:Level01``).
Mỗi level có nhiều attempt cho đến khi ``Complete`` hoặc ``Abandon``.

    Attempt 1 → Start → Fail
    Attempt 2 → Start → Complete

Module này là "bộ não" quyết định cho từng step trong 1 session:

    sim = ProgressionSimulator(player, session_start_ts, rng)
    for ts in session_timestamps:
        action = sim.next_action(client_ts=ts)
        if action is None:
            break           # session hết thời gian
        events.append(generate_progression_event(player, session, action))

Quy tắc áp dụng:
    - Số level mỗi session theo engagement (LEVEL_PROGRESSION_SPEED):
        Casual   : 0.5–1.5 level/ngày
        Regular  : 1–3 level/ngày
        Engaged  : 2–5 level/ngày
        Hardcore : 4–8 level/ngày
    - Xác suất Complete mỗi attempt theo ATTEMPT_SUCCESS_PROBABILITY:
        Casual   : 0.55
        Regular  : 0.65
        Engaged  : 0.75
        Hardcore : 0.85
    - Sau khi Complete: reset attempt_num = 1, current_level += 1
    - Sau khi Fail: attempt_num += 1 (giữ nguyên level)
    - attempt_num > MAX_ATTEMPTS_PER_LEVEL: ABANDON level, skip sang level sau
      (reset attempt_num = 1, current_level += 1)

Action object:
    ProgressionAction(
        client_ts=ts,
        event_id="Start:World1:Level05",
        start_system=1,
        end_system=5,
        complete_status="Start" | "Fail" | "Complete",
        attempt_num=1,
    )
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from config import player_config


# =========================================================================
# Constants – mirror INSTRUCTION example
# =========================================================================
START: str = "Start"
FAIL: str = "Fail"
COMPLETE: str = "Complete"
ABANDON: str = "Abandon"  # event_id suffix khi abandon (chỉ dùng cho logging)


@dataclass
class ProgressionAction:
    """Một hành động progression cần phát ra event.

    Attributes:
        client_ts: unix timestamp gửi event.
        event_id: string dạng "Start:World1:Level05" – KHÔNG chứa UUID,
                  timestamp, user_id.
        start_system: world_id bắt đầu (int).
        end_system: level_id kết thúc (int).
        complete_status: trạng thái progression: Start / Fail / Complete.
        attempt_num: attempt hiện tại (1..MAX_ATTEMPTS_PER_LEVEL).
    """

    client_ts: int
    event_id: str
    start_system: int
    end_system: int
    complete_status: str
    attempt_num: int


def _build_event_id(status: str, world: int, level: int) -> str:
    """Sinh event_id theo format INSTRUCTION: ``Status:World<n>:Level<nn>``.

    Ví dụ:
        >>> _build_event_id("Start", 1, 5)
        'Start:World1:Level05'
    """
    return f"{status}:World{world}:Level{level:02d}"


def _levels_to_play_this_session(engagement_segment: str, rng: random.Random) -> int:
    """Số level attempt sẽ chơi trong session này.

    Dựa trên LEVEL_PROGRESSION_SPEED – trả về số level nguyên >= 1
    (sample uniform trong range của segment).
    """
    min_speed, max_speed = player_config.LEVEL_PROGRESSION_SPEED[engagement_segment]
    raw = rng.uniform(min_speed, max_speed)
    return max(1, int(round(raw)))


def _success_probability(engagement_segment: str) -> float:
    """Xác suất Complete mỗi attempt theo engagement segment."""
    return player_config.ATTEMPT_SUCCESS_PROBABILITY[engagement_segment]


# =========================================================================
# Progression Simulator
# =========================================================================

class ProgressionSimulator:
    """Quản lý state progression cho 1 player trong 1 session.

    State lưu trên chính Player model:
        - player.current_world    : world hiện tại
        - player.current_level    : level trong world hiện tại (1..LEVELS_PER_WORLD)
        - player.current_attempt_num: attempt của level hiện tại

    Instance state riêng cho session:
        - self._remaining_levels : số level sẽ chơi còn lại trong session
        - self._session_started   : đã phát event Start đầu tiên chưa
    """

    def __init__(
        self,
        player: Any,  # Player model
        session_start_ts: int,
        rng: random.Random | None = None,
    ) -> None:
        """Khởi tạo simulator.

        Args:
            player: Player model (đã có current_world, current_level,
                    current_attempt_num, engagement_segment).
            session_start_ts: unix timestamp session bắt đầu.
            rng: random.Random instance. Mặc định tạo mới.
        """
        if rng is None:
            rng = random.Random()

        self._player = player
        self._session_start_ts = session_start_ts
        self._rng = rng
        # Bao nhiêu level sẽ chơi trong session này
        self._remaining_levels = _levels_to_play_this_session(
            player.engagement_segment, rng
        )
        # Đánh dấu đã phát event Start cho level hiện tại hay chưa
        self._session_started = False
        self._current_level_finished = False  # level hiện tại đã Complete/Abandon chưa

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    @property
    def world(self) -> int:
        return self._player.current_world

    @property
    def level(self) -> int:
        return self._player.current_level

    @property
    def attempt_num(self) -> int:
        return self._player.current_attempt_num

    @property
    def remaining_levels(self) -> int:
        return self._remaining_levels

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def next_action(self, client_ts: int) -> ProgressionAction | None:
        """Trả về action progression tiếp theo, hoặc None nếu hết session.

        Args:
            client_ts: unix timestamp hiện tại trong session.

        Returns:
            ProgressionAction cần phát event, hoặc None nếu đã chơi đủ
            level cho session này.
        """
        # Session đã chơi hết số level dự kiến -> dừng
        if self._remaining_levels <= 0 and self._current_level_finished:
            return None

        # CHƯA phát Start cho level hiện tại -> phát Start
        if not self._session_started:
            self._session_started = True
            self._current_level_finished = False
            return ProgressionAction(
                client_ts=client_ts,
                event_id=_build_event_id(START, self.world, self.level),
                start_system=self.world,
                end_system=self.level,
                complete_status=START,
                attempt_num=self.attempt_num,
            )

        # ĐÃ phát Start -> resolve thành Fail hoặc Complete
        success_prob = _success_probability(self._player.engagement_segment)
        succeeded = self._rng.random() < success_prob

        if succeeded:
            # COMPLETE: reset attempt_num về 1, current_level += 1
            self._player.current_attempt_num = 1
            self._advance_level()
            self._session_started = False
            self._current_level_finished = True
            return ProgressionAction(
                client_ts=client_ts,
                event_id=_build_event_id(COMPLETE, self.world, self.level),
                start_system=self.world,
                end_system=self.level,
                complete_status=COMPLETE,
                attempt_num=self.attempt_num,
            )

        # FAIL: attempt_num += 1
        self._player.current_attempt_num += 1

        # Nếu vượt MAX_ATTEMPTS_PER_LEVEL -> abandon
        if self._player.current_attempt_num > player_config.MAX_ATTEMPTS_PER_LEVEL:
            # Abandon: reset attempt_num, level += 1, KHÔNG phát event Complete
            self._player.current_attempt_num = 1
            self._advance_level()
            self._session_started = False
            self._current_level_finished = True
            # Không phát event Fail nào nữa cho attempt này – đây là nội bộ
            # Simulator. Caller loop sẽ phát Start cho level kế tiếp.
            return None

        # Phát Fail event
        return ProgressionAction(
            client_ts=client_ts,
            event_id=_build_event_id(FAIL, self.world, self.level),
            start_system=self.world,
            end_system=self.level,
            complete_status=FAIL,
            attempt_num=self.attempt_num,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _advance_level(self) -> None:
        """Tăng current_level, hoặc current_world nếu hết level trong world."""
        self._player.current_level += 1
        self._remaining_levels -= 1
        if self._player.current_level > player_config.LEVELS_PER_WORLD:
            self._player.current_world += 1
            self._player.current_level = 1
            # Vượt quá MAX_LEVEL: quay lại World 1 Level 1
            if self._player.current_world > player_config.WORLD_COUNT:
                self._player.current_world = 1
                self._player.current_level = 1


__all__ = [
    "ProgressionAction",
    "ProgressionSimulator",
    "START",
    "FAIL",
    "COMPLETE",
    "ABANDON",
]
