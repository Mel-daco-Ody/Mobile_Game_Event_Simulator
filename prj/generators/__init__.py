"""Generators package – sinh dữ liệu cho simulation.

Hiện tại:
    - player_generator:        sinh tập players khởi đầu (Bước 3)
    - user_event_generator:    sinh event 'user' cho session start (Bước 7)
    - session_end_generator:   sinh event 'session_end' cuối session (Bước 8)
    - progression_generator:   sinh event 'progression' Start/Fail/Complete (Bước 9)
    - resource_generator:      sinh event 'resource' Source/Sink (Bước 10)
    - business_generator:      sinh event 'business' cho IAP (Bước 11)
    - ad_generator:            sinh event 'ad' cho impression (Bước 12)

Các generator khác (design, error) sẽ được bổ sung ở
BƯỚC 13–14 theo INSTRUCTION.md.
"""

from __future__ import annotations

from .ad_generator import (
    REWARDED_SOURCE_ITEMS,
    build_rewarded_resource_tx,
    generate_ad_event,
)
from .business_generator import generate_business_event
from .design_generator import generate_design_event
from .error_generator import generate_error_event
from .player_generator import (
    generate_one_player,
    generate_players,
    summarize_players,
)
from .progression_generator import generate_progression_event
from .resource_generator import generate_resource_event
from .session_end_generator import generate_session_end_event
from .user_event_generator import generate_user_event

__all__ = [
    # Player
    "generate_one_player",
    "generate_players",
    "summarize_players",
    # User event (Bước 7)
    "generate_user_event",
    # Session end (Bước 8)
    "generate_session_end_event",
    # Progression (Bước 9)
    "generate_progression_event",
    # Resource (Bước 10)
    "generate_resource_event",
    # Business (Bước 11)
    "generate_business_event",
    # Ad (Bước 12)
    "generate_ad_event",
    "build_rewarded_resource_tx",
    "REWARDED_SOURCE_ITEMS",
    # Design (Bước 13)
    "generate_design_event",
    # Error (Bước 14)
    "generate_error_event",
]
