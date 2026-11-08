"""Error Event Simulator – mô phỏng debug/info/warning/error/critical events.

Theo BƯỚC 14 INSTRUCTION.md:

Mô phỏng các sự kiện error từ game (network, asset, save, config, null ref).

Quy tắc:
    - severity ∈ {debug, info, warning, error, critical} (weighted).
    - message ∈ tập ERROR_MESSAGES cố định (5 message).
    - Xác suất cực nhỏ: ERROR_PROBABILITY_PER_EVENT = 0.001 (0.1%).
    - KHÔNG biến simulator thành "Error Generator" – giới hạn
      MAX_ERROR_EVENTS_PER_SESSION (mặc định 1).
    - Có thể chỉ trigger tại các "error-prone" gameplay checkpoint
      (network call, asset load, save sync) thay vì spam mỗi event.

State máy:
    ErrorSimulator stateless – caller truyền already_emitted_in_session
    để enforce cap. Việc này giúp dễ test và consistent với persistence
    ở storage layer (Bước 17+).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from config import event_config


# =========================================================================
# Error Decision (data carrier)
# =========================================================================

@dataclass
class ErrorDecision:
    """Quyết định phát sinh 1 error event.

    Attributes:
        client_ts: unix timestamp.
        severity: 'debug' | 'info' | 'warning' | 'error' | 'critical'.
        message: 'network_timeout' | 'asset_load_failed'
                 | 'save_sync_failed' | 'config_parse_error'
                 | 'unexpected_null_reference'.
        stack_trace: str – CÓ thể None cho debug/info. Cho warning trở lên
                     nên attach 1 short snapshot.
    """

    client_ts: int
    severity: str
    message: str
    stack_trace: str | None = None


# =========================================================================
# Mapping: severity -> có stack_trace không
# =========================================================================
# Theo nguyên tắc GameAnalytics:
#   - debug / info : kèm stack_trace ngắn
#   - warning      : kèm 1 line context
#   - error / critical: stack_trace dạng "File:<file> Line:<line>"
_STACK_TRACE_FOR_SEVERITY = {
    "debug":    "verbose",
    "info":     "verbose",
    "warning":  "short",
    "error":    "stack",
    "critical": "stack",
}


# =========================================================================
# Error Simulator
# =========================================================================

class ErrorSimulator:
    """Sinh error event phụ thuộc vào gameplay checkpoint.

    Stateless – caller quản lý cap thông qua tham số
    `already_emitted_in_session`.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        if rng is None:
            rng = random.Random()
        self._rng = rng

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def try_event(
        self,
        client_ts: int,
        already_emitted_in_session: int = 0,
        prob_override: float | None = None,
    ) -> ErrorDecision | None:
        """Thử phát 1 error event.

        Quyết định:
            1. Nếu already_emitted_in_session >= MAX_ERROR_EVENTS_PER_SESSION
               → return None (cap).
            2. Roll dice `prob_override` (hoặc ERROR_PROBABILITY_PER_EVENT
               mặc định). Nếu fail → return None.
            3. Pick severity theo ERROR_SEVERITY_WEIGHTS.
            4. Pick message theo ERROR_MESSAGES (uniform).

        Args:
            client_ts: unix timestamp.
            already_emitted_in_session: số error event đã phát trong
                session hiện tại. Cap mặc định = 1.
            prob_override: optional – nếu None dùng
                ERROR_PROBABILITY_PER_EVENT (0.001).

        Returns:
            ErrorDecision hoặc None.
        """
        # 1. Cap check
        max_per_session = event_config.MAX_ERROR_EVENTS_PER_SESSION
        if already_emitted_in_session >= max_per_session:
            return None

        # 2. Roll dice
        prob = (prob_override
                if prob_override is not None
                else event_config.ERROR_PROBABILITY_PER_EVENT)
        if prob <= 0 or self._rng.random() >= prob:
            return None

        # 3. Pick severity (weighted)
        severity = self._rng.choices(
            event_config.ERROR_SEVERITY_LEVELS,
            weights=event_config.ERROR_SEVERITY_WEIGHTS,
            k=1,
        )[0]

        # 4. Pick message (uniform)
        message = self._rng.choice(event_config.ERROR_MESSAGES)

        # 5. Build stack_trace tùy severity
        stack_trace = self._build_stack_trace(severity, message)

        return ErrorDecision(
            client_ts=client_ts,
            severity=severity,
            message=message,
            stack_trace=stack_trace,
        )

    def try_events_for_session(
        self,
        client_start_ts: int,
        n_attempts: int = 200,
        ts_step_seconds: int = 5,
    ) -> list[ErrorDecision]:
        """Thử phát error event tại nhiều checkpoint trong session.

        Đây là helper "naive" – chỉ dùng để test statistic. Caller
        trong production nên gọi try_event() với cap từ storage layer.

        Args:
            client_start_ts: timestamp đầu session.
            n_attempts: số lần thử roll.
            ts_step_seconds: khoảng cách giữa các lần thử (giây).

        Returns:
            list ErrorDecision (size ≤ MAX_ERROR_EVENTS_PER_SESSION).
        """
        decisions: list[ErrorDecision] = []
        for i in range(n_attempts):
            ts = client_start_ts + i * ts_step_seconds
            d = self.try_event(ts, already_emitted_in_session=len(decisions))
            if d is not None:
                decisions.append(d)
                # Cap
                if len(decisions) >= event_config.MAX_ERROR_EVENTS_PER_SESSION:
                    break
        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_stack_trace(self, severity: str, message: str) -> str | None:
        """Sinh stack_trace rút gọn theo severity.

        Theo INSTRUCTION, KHÔNG nên gắn UUID/timestamp/user_id vào event
        hierarchy. Stack trace chỉ nên chứa tên file + số dòng (cố định).
        """
        kind = _STACK_TRACE_FOR_SEVERITY.get(severity, "short")
        if kind == "verbose":
            return f"Engine.handle({message})"
        if kind == "short":
            return f"Engine.handle({message}): step=retry"
        # stack – chỉ tên file ảo, KHÔNG timestamp/user_id
        return (f"NetworkManager.cs:142 | "
                f"AssetLoader.cs:88 | "
                f"SaveManager.cs:55 | handler={message}")


__all__ = ["ErrorSimulator", "ErrorDecision"]
