"""Config package – tổng hợp các file cấu hình.

Các file config nằm cùng package sẽ được tự động import.
Mỗi file config đặt một số CONSTANT ở module-level (ví dụ PLAYER_COUNT)
để các module khác có thể truy cập qua:
    from config import game_config, player_config

Package này KHÔNG tự load submodule trừ khi __getattr__ được gọi
(lazy import), tránh tạo vòng import giữa các file config.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any

__all__: list[str] = []


def _discover_config_modules() -> list[str]:
    """Tự động tìm các module config trong package này.

    Trả về danh sách tên module (không bao gồm 'config' bản thân nó,
    không bao gồm smoke_test.py, không bao gồm __init__).
    """
    discovered: list[str] = []
    for module_info in pkgutil.iter_modules(__path__):  # type: ignore[attr-defined]
        name = module_info.name
        # Bỏ qua các module không phải config thuần
        if name.startswith("_") or name in {"smoke_test"}:
            continue
        discovered.append(name)
    return discovered


# Lưu danh sách đã phát hiện để __getattr__ sử dụng
_CONFIG_MODULES: list[str] = _discover_config_modules()


def __getattr__(name: str) -> Any:
    """Lazy import các module config khi được truy cập lần đầu.

    Cho phép:
        from config import game_config
    mà không cần liệt kê thủ công.
    """
    if name in _CONFIG_MODULES:
        module: ModuleType = importlib.import_module(f".{name}", __name__)
        globals()[name] = module  # cache lại cho lần sau
        return module
    raise AttributeError(f"module 'config' has no attribute {name!r}")


def list_configs() -> list[str]:
    """Trả về danh sách tên các file config đã được phát hiện."""
    return list(_CONFIG_MODULES)


# Xuất tên các module ra __all__ để IDE / star import nhận biết
__all__.extend(_CONFIG_MODULES)


# =========================================================================
# Re-export credentials (Bước 1 bổ sung – INSTRUCTION §1.2)
# =========================================================================
# Lý do re-export ở đây: cho phép ``from config import get_credentials``
# (ngắn gọn hơn ``from config.credentials import get_credentials``)
# mà vẫn giữ lazy semantics (chỉ load khi user thực sự truy cập).
from .credentials import (  # noqa: E402
    DEFAULT_GA_ENV,
    DEFAULT_ENV_PATHS,
    ENV_GA_ENV,
    ENV_GAME_KEY,
    ENV_SECRET_KEY,
    VALID_GA_ENVS,
    GameAnalyticsCredentials,
    clear_cache as clear_credentials_cache,
    find_env_file,
    get_credentials,
    get_ga_env,
    load_credentials,
    load_env_file,
    require_game_key,
    require_secret_key,
)

__all__.extend([
    "GameAnalyticsCredentials",
    "get_credentials",
    "load_credentials",
    "load_env_file",
    "require_game_key",
    "require_secret_key",
    "get_ga_env",
    "find_env_file",
    "clear_credentials_cache",
    "DEFAULT_GA_ENV",
    "DEFAULT_ENV_PATHS",
    "ENV_GAME_KEY",
    "ENV_SECRET_KEY",
    "ENV_GA_ENV",
    "VALID_GA_ENVS",
])
