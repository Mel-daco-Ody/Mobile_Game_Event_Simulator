"""Credentials loader – đọc GAME_KEY / SECRET_KEY từ file .env.

Theo INSTRUCTION §1.2:

    Đăng ký game tại GameAnalytics → lấy ``GAME_KEY`` và ``SECRET_KEY``
    từ Game Settings.

    Tạo file ``.env``::

        GAME_KEY=your_game_key
        SECRET_KEY=your_secret_key
        GA_ENV=sandbox

    KHÔNG hard-code SECRET_KEY trong source code.

Module này cung cấp:

    - ``GameAnalyticsCredentials``: dataclass chứa 3 field
    - ``load_credentials(path)``: đọc file .env thủ công
      (không cần ``python-dotenv`` – tự parse để tránh thêm dep)
    - ``get_credentials()``: lazy load + cache singleton
    - ``require_game_key()`` / ``require_secret_key()``: tiện ích
    - ``GameAnalyticsClient.from_env()``: classmethod tiện lợi

Tích hợp::

    # Cách 1: truyền thẳng (Bước 20 hiện tại)
    from config import game_config
    from gameanalytics import GameAnalyticsClient
    from config.credentials import get_credentials
    creds = get_credentials()
    client = GameAnalyticsClient(
        game_key=creds.game_key,
        secret_key=creds.secret_key,
        environment=creds.environment,
    )

    # Cách 2: classmethod from_env
    from gameanalytics import GameAnalyticsClient
    client = GameAnalyticsClient.from_env()
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Environment variable names (theo INSTRUCTION §1.2)
ENV_GAME_KEY: str = "GAME_KEY"
ENV_SECRET_KEY: str = "SECRET_KEY"
ENV_GA_ENV: str = "GA_ENV"

# Default environment (per INSTRUCTION: dùng sandbox ở dev)
DEFAULT_GA_ENV: str = "sandbox"

# Valid environments (theo game_config.GA_BASE_URLS keys)
VALID_GA_ENVS: frozenset[str] = frozenset({"sandbox", "production"})

# Default .env paths (tìm theo thứ tự)
DEFAULT_ENV_PATHS: tuple[str, ...] = (
    ".env",                            # cwd
    "prj/.env",                        # nếu chạy từ root project
    "../.env",                         # nếu cwd ở subfolder
    "data/.env",                       # optional
)


# =========================================================================
# Dataclass
# =========================================================================

@dataclass(frozen=True)
class GameAnalyticsCredentials:
    """Bundle chứa 3 credentials từ GameAnalytics.

    Attributes:
        game_key: GAME_KEY từ Game Settings.
        secret_key: SECRET_KEY từ Game Settings.
        environment: "sandbox" hoặc "production".
        source: nguồn load ("env_file:<path>" hoặc "process_env" hoặc
                "default:<field>"). Dùng cho debug.
    """

    game_key: str
    secret_key: str
    environment: str = DEFAULT_GA_ENV
    source: str = "default"

    def __post_init__(self) -> None:
        if not self.game_key:
            raise ValueError(
                "GAME_KEY rỗng. Kiểm tra file .env hoặc biến môi trường."
            )
        if not self.secret_key:
            raise ValueError(
                "SECRET_KEY rỗng. Kiểm tra file .env hoặc biến môi trường."
            )
        if self.environment not in VALID_GA_ENVS:
            raise ValueError(
                f"environment '{self.environment}' không hợp lệ. "
                f"Chọn 1 trong {sorted(VALID_GA_ENVS)}"
            )

    @property
    def is_sandbox(self) -> bool:
        return self.environment == "sandbox"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def masked(self) -> "GameAnalyticsCredentials":
        """Trả bản copy với secret_key đã mask (cho log)."""
        return GameAnalyticsCredentials(
            game_key=self.game_key[:4] + "***" if self.game_key else "",
            secret_key="***MASKED***",
            environment=self.environment,
            source=self.source,
        )


# =========================================================================
# .env parser (manual, không cần python-dotenv)
# =========================================================================

def _parse_env_file(content: str) -> dict[str, str]:
    """Parse nội dung file .env thành dict.

    Hỗ trợ:
        - KEY=value
        - KEY="value with spaces"
        - KEY='value with quotes'
        - Comment dòng bắt đầu bằng ``#``
        - Dòng trống bị bỏ qua
        - Khoảng trắng quanh ``=`` được strip

    KHÔNG hỗ trợ:
        - Variable expansion (${OTHER_VAR})
        - Multi-line value
        - Escape sequence

    Args:
        content: nội dung file .env.

    Returns:
        dict {KEY: value}. Value là str đã strip quote.
    """
    result: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip quotes (single hoặc double)
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        result[key] = value
    return result


def load_env_file(path: str | Path) -> dict[str, str]:
    """Đọc file .env và trả dict key-value.

    Args:
        path: đường dẫn file .env.

    Returns:
        dict các biến đọc được.

    Raises:
        FileNotFoundError: nếu file không tồn tại.
        ValueError: nếu file không parse được (vd: binary).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"File .env không tồn tại: {p.absolute()}"
        )
    if not p.is_file():
        raise ValueError(f"Path không phải file: {p.absolute()}")
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(
            f"File .env không phải UTF-8: {e}. "
            f"Kiểm tra encoding (nên dùng UTF-8 no BOM)."
        ) from e
    return _parse_env_file(content)


def find_env_file() -> Path | None:
    """Tìm file .env theo DEFAULT_ENV_PATHS.

    Returns:
        Path nếu tìm thấy, None nếu không.
    """
    for rel in DEFAULT_ENV_PATHS:
        p = Path(rel)
        if p.exists() and p.is_file():
            return p.resolve()
    return None


# =========================================================================
# Loading logic
# =========================================================================

def _load_from_env_var() -> dict[str, str]:
    """Đọc từ process environment (set trước khi chạy)."""
    return {
        k: os.environ[k]
        for k in (ENV_GAME_KEY, ENV_SECRET_KEY, ENV_GA_ENV)
        if k in os.environ
    }


def _load_from_file(path: str | Path) -> dict[str, str]:
    """Đọc từ file .env."""
    return load_env_file(path)


def load_credentials(
    env_path: str | Path | None = None,
    require_all: bool = True,
) -> GameAnalyticsCredentials:
    """Load GameAnalyticsCredentials từ process env HOẶC file .env.

    Thứ tự ưu tiên (cao → thấp):
        1. Biến môi trường process (``os.environ``) — cho production/CI
        2. File .env (nếu chỉ định ``env_path``)
        3. File .env tự tìm theo DEFAULT_ENV_PATHS

    Args:
        env_path: đường dẫn file .env. None = tự tìm.
        require_all: True → raise nếu thiếu GAME_KEY hoặc SECRET_KEY.
                     False → trả credentials với default (debug).

    Returns:
        GameAnalyticsCredentials.

    Raises:
        FileNotFoundError: nếu require_all=True và không tìm thấy
                           .env cũng như env var.
        ValueError: nếu values không hợp lệ (rỗng, environment lạ).
    """
    merged: dict[str, str] = {}
    source_parts: list[str] = []

    # 1. Process env
    from_env = _load_from_env_var()
    if from_env:
        merged.update(from_env)
        source_parts.append("process_env")

    # 2. Từ file
    path_used: Path | None = None
    if env_path is not None:
        path_used = Path(env_path)
    else:
        path_used = find_env_file()

    if path_used is not None:
        try:
            from_file = _load_from_file(path_used)
            # Env var luôn thắng file
            for k, v in from_file.items():
                if k not in merged:
                    merged[k] = v
            source_parts.append(f"env_file:{path_used}")
        except FileNotFoundError:
            if not merged:
                # Không có gì để fallback
                if require_all:
                    raise
                path_used = None

    # 3. Default environment
    if ENV_GA_ENV not in merged:
        merged[ENV_GA_ENV] = DEFAULT_GA_ENV

    source = "+".join(source_parts) if source_parts else "default"

    # 4. Validate
    if require_all:
        if not merged.get(ENV_GAME_KEY):
            raise ValueError(
                f"{ENV_GAME_KEY} chưa được set. "
                f"Tạo file .env với GAME_KEY=... hoặc "
                f"export GAME_KEY=... trước khi chạy."
            )
        if not merged.get(ENV_SECRET_KEY):
            raise ValueError(
                f"{ENV_SECRET_KEY} chưa được set. "
                f"Tạo file .env với SECRET_KEY=... hoặc "
                f"export SECRET_KEY=... trước khi chạy. "
                f"INSTRUCTION: KHÔNG hard-code SECRET_KEY."
            )

    return GameAnalyticsCredentials(
        game_key=merged.get(ENV_GAME_KEY, ""),
        secret_key=merged.get(ENV_SECRET_KEY, ""),
        environment=merged.get(ENV_GA_ENV, DEFAULT_GA_ENV),
        source=source,
    )


# =========================================================================
# Singleton + accessor
# =========================================================================

_cached_credentials: GameAnalyticsCredentials | None = None
_cached_env_path: str | Path | None = None


def get_credentials(
    env_path: str | Path | None = None,
    force_reload: bool = False,
) -> GameAnalyticsCredentials:
    """Lazy load + cache credentials (singleton).

    Args:
        env_path: đường dẫn .env (None = tự tìm).
        force_reload: True → bỏ qua cache, đọc lại.

    Returns:
        GameAnalyticsCredentials (đã validate).
    """
    global _cached_credentials, _cached_env_path
    if (
        _cached_credentials is None
        or force_reload
        or (env_path is not None and env_path != _cached_env_path)
    ):
        _cached_credentials = load_credentials(env_path=env_path)
        _cached_env_path = env_path
    return _cached_credentials


def require_game_key() -> str:
    """Trả GAME_KEY (raise nếu chưa có)."""
    return get_credentials().game_key


def require_secret_key() -> str:
    """Trả SECRET_KEY (raise nếu chưa có)."""
    return get_credentials().secret_key


def get_ga_env() -> str:
    """Trả GA_ENV (sandbox/production)."""
    return get_credentials().environment


def clear_cache() -> None:
    """Reset cache (dùng cho test)."""
    global _cached_credentials, _cached_env_path
    _cached_credentials = None
    _cached_env_path = None


# =========================================================================
# Auto-load on import (optional, kiểm soát qua biến môi trường)
# =========================================================================

# Nếu user set ``GA_AUTO_LOAD_CREDENTIALS=1`` → tự load ngay khi import.
# Mặc định KHÔNG auto-load để:
#   - Không crash khi test (không có .env test)
#   - Cho phép caller chọn lúc load (lazy pattern)
_AUTO_LOAD_ENV_VAR: str = "GA_AUTO_LOAD_CREDENTIALS"


def _maybe_auto_load() -> None:
    """Auto-load credentials nếu được yêu cầu qua env var."""
    if os.environ.get(_AUTO_LOAD_ENV_VAR, "").lower() in ("1", "true", "yes"):
        try:
            get_credentials(force_reload=True)
        except (FileNotFoundError, ValueError):
            # Im lặng nếu auto-load fail – caller có thể xử lý sau
            pass


_maybe_auto_load()


__all__ = [
    "DEFAULT_GA_ENV",
    "DEFAULT_ENV_PATHS",
    "ENV_GA_ENV",
    "ENV_GAME_KEY",
    "ENV_SECRET_KEY",
    "GameAnalyticsCredentials",
    "VALID_GA_ENVS",
    "clear_cache",
    "find_env_file",
    "get_credentials",
    "get_ga_env",
    "load_credentials",
    "load_env_file",
    "require_game_key",
    "require_secret_key",
]
