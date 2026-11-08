"""Models package – định nghĩa các dataclass cho domain.

Hiện tại:
    - Player: mô hình một player trong simulation (Bước 2)
    - Session: cấu trúc một gameplay session (Bước 5)
"""

from __future__ import annotations

from .player import (
    ACQUISITION_SOURCES,
    CHURN_STATUS_ACTIVE,
    CHURN_STATUS_CHURNED,
    CHURN_STATUS_DORMANT,
    CHURN_STATUSES,
    ENGAGEMENT_SEGMENTS,
    LIFECYCLE_SEGMENTS,
    PAYER_SEGMENTS,
    Player,
)
from .session import (
    SESSION_DURATION_SECONDS,
    Session,
    generate_session,
)

__all__ = [
    # Player
    "Player",
    "PAYER_SEGMENTS",
    "ENGAGEMENT_SEGMENTS",
    "LIFECYCLE_SEGMENTS",
    "ACQUISITION_SOURCES",
    "CHURN_STATUSES",
    "CHURN_STATUS_ACTIVE",
    "CHURN_STATUS_DORMANT",
    "CHURN_STATUS_CHURNED",
    # Session
    "Session",
    "SESSION_DURATION_SECONDS",
    "generate_session",
]
