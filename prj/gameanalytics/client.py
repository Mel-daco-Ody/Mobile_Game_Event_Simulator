"""GameAnalytics Client – gọi API và xử lý response.

Theo BƯỚC 20 INSTRUCTION.md:

Tạo 2 method:

1. ``POST /v2/<game_key>/init`` — gọi khi bắc đầu session / launch::

    Session Start
      ↓
    Init
      ↓
    server_ts
      ↓
    Calculate clock offset = server_ts - local_timestamp
      ↓
    client_ts = local_event_timestamp + offset   (khi tạo event)

    Response có thể chứa ``configs_hash`` → lưu lại nếu triển khai
    Remote Config / A-B Testing. Ở phase đầu có thể bỏ qua.

2. ``POST /v2/<game_key>/events`` — gửi batch::

    Body = JSON list (KHÔNG phải object).
    Response mong đợi: ``HTTP 200 OK``.

Log mỗi request::

    batch_id
    event_count
    payload_size
    status_code
    timestamp
    retry_count

Retry (Bước 21): KHÔNG có ở client này – caller bọc ngoài để quản
lý backoff + dead-letter. Client chỉ throw exception với status_code.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from config import game_config

# Lazy imports để tránh circular + startup cost:
# - auth + compressor + annotations chỉ cần khi thực sự gọi send
# - smoke test có thể patch client mà không cần load deps khác
#
# ``requests`` chỉ import khi cần HTTP call thật. Tránh:
#   - Crash khi urllib3 yêu cầu runtime features (queue.LifoQueue, ...)
#     trên môi trường mock / offline
#   - Circular import khi package khác import gameanalytics.annotations
if TYPE_CHECKING:  # chỉ để type checker biết
    import requests as _requests  # noqa: F401


def _get_requests():
    """Lazy import requests (chỉ khi cần HTTP thật)."""
    import requests as _r
    return _r

logger = logging.getLogger(__name__)

# Endpoint templates (config-driven, có thể override cho test)
DEFAULT_INIT_PATH: str = game_config.INIT_ENDPOINT
DEFAULT_EVENTS_PATH: str = game_config.EVENTS_ENDPOINT

# HTTP defaults
DEFAULT_TIMEOUT_SEC: float = 30.0
DEFAULT_USER_AGENT: str = game_config.SDK_VERSION

# Status codes GameAnalytics trả về (theo collection API docs)
GA_OK: int = 200
GA_BAD_REQUEST: int = 400
GA_UNAUTHORIZED: int = 401
GA_FORBIDDEN: int = 403
GA_NOT_FOUND: int = 404
GA_PAYLOAD_TOO_LARGE: int = 413
GA_SERVER_ERROR_MIN: int = 500
GA_SERVER_ERROR_MAX: int = 599


# =========================================================================
# Exceptions
# =========================================================================

class GameAnalyticsError(Exception):
    """Exception tổng quát cho GameAnalytics client.

    Attributes:
        status_code: HTTP status (None nếu lỗi mạng / timeout).
        body: response body (string, có thể rỗng).
        request_log: log request đã gửi (nếu có).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str = "",
        request_log: "RequestLog | None" = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.request_log = request_log

    def __str__(self) -> str:
        if self.status_code is not None:
            return (
                f"GameAnalyticsError({self.status_code}): {self.args[0]} "
                f"| body={self.body[:200]!r}"
            )
        return f"GameAnalyticsError(network): {self.args[0]}"


class GameAnalyticsAuthError(GameAnalyticsError):
    """Lỗi 401/403 – sai game_key hoặc secret_key."""
    pass


class GameAnalyticsPayloadTooLargeError(GameAnalyticsError):
    """Lỗi 413 – body vượt 1 MB."""
    pass


class GameAnalyticsServerError(GameAnalyticsError):
    """Lỗi 5xx – server GameAnalytics gặp vấn đề, nên retry."""
    pass


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class InitResult:
    """Kết quả từ ``init`` call.

    Attributes:
        server_ts: unix timestamp từ GameAnalytics server.
        local_ts: unix timestamp local tại lúc gọi init.
        offset: server_ts - local_ts (giây).
        configs_hash: hash của remote config (None nếu không có).
        raw_response: dict response gốc (cho debug).
    """

    server_ts: int
    local_ts: int
    offset: int
    configs_hash: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def local_iso(self) -> str:
        """Local timestamp dạng ISO 8601 (UTC)."""
        return (
            datetime.fromtimestamp(self.local_ts, tz=timezone.utc)
            .isoformat()
        )

    @property
    def server_iso(self) -> str:
        """Server timestamp dạng ISO 8601 (UTC)."""
        return (
            datetime.fromtimestamp(self.server_ts, tz=timezone.utc)
            .isoformat()
        )


@dataclass
class RequestLog:
    """Log cho 1 HTTP request (INSTRUCTION yêu cầu).

    Attributes:
        batch_id: id định danh batch (None cho init).
        endpoint: path đã gọi (vd: ``/v2/<game_key>/events``).
        event_count: số event trong body.
        payload_size: size của body đã gửi (bytes).
        status_code: HTTP status (None nếu lỗi mạng).
        timestamp: unix timestamp local lúc gửi.
        retry_count: số lần đã retry (do caller quản lý).
        duration_ms: thời gian request (ms).
        error: error message (nếu fail).
        request_kind: ``"init"`` hoặc ``"events"``.
    """

    request_kind: str
    endpoint: str
    payload_size: int
    timestamp: float
    event_count: int = 0
    batch_id: str | None = None
    status_code: int | None = None
    retry_count: int = 0
    duration_ms: float | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_success(self) -> bool:
        return self.status_code == GA_OK and self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Serialize thành dict (cho logging / DB)."""
        return {
            "request_kind": self.request_kind,
            "batch_id": self.batch_id,
            "endpoint": self.endpoint,
            "event_count": self.event_count,
            "payload_size": self.payload_size,
            "status_code": self.status_code,
            "timestamp": self.timestamp,
            "timestamp_iso": (
                datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
            ),
            "retry_count": self.retry_count,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "extra": self.extra,
        }


@dataclass
class SendResult:
    """Kết quả từ ``send_events`` call.

    Attributes:
        success: True nếu status_code == 200.
        status_code: HTTP status code.
        batch_id: id định danh batch.
        event_count: số event đã gửi.
        payload_size: size body (bytes).
        retry_count: số lần retry (caller quản lý).
        timestamp: unix timestamp local.
        error: error message (nếu có).
        request_log: log chi tiết.
    """

    success: bool
    status_code: int | None
    batch_id: str
    event_count: int
    payload_size: int
    retry_count: int
    timestamp: float
    error: str | None = None
    request_log: RequestLog | None = None


# =========================================================================
# GameAnalyticsClient
# =========================================================================

class GameAnalyticsClient:
    """HTTP client cho GameAnalytics Collection API v2.

    Usage::

        client = GameAnalyticsClient(
            game_key="...",
            secret_key="...",
            environment="sandbox",
        )
        init_result = client.init()                 # tính offset
        send_result = client.send_events(events)    # gửi batch

    Với dependency injection cho test::

        client = GameAnalyticsClient(
            game_key="...", secret_key="...",
            http_session=mock_session,             # requests.Session stub
            time_source=lambda: 1759305600.0,
            id_generator=lambda: "batch_001",
        )
    """

    def __init__(
        self,
        game_key: str,
        secret_key: str,
        environment: str = "sandbox",
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        user_agent: str = DEFAULT_USER_AGENT,
        http_session: "requests.Session | None" = None,
        time_source: Callable[[], float] | None = None,
        id_generator: Callable[[], str] | None = None,
    ) -> None:
        """Khởi tạo client.

        Args:
            game_key: GAME_KEY GameAnalytics cấp.
            secret_key: SECRET_KEY GameAnalytics cấp.
            environment: "sandbox" hoặc "production" (dùng khi base_url=None).
            base_url: URL gốc (vd: ``https://httpbin.org`` cho test). Mặc định
                      lấy từ ``game_config.GA_BASE_URLS[environment]``.
            timeout: HTTP timeout (giây).
            user_agent: User-Agent header.
            http_session: requests.Session (cho dependency injection / test).
            time_source: callable trả float unix ts (cho test deterministic).
            id_generator: callable trả batch_id string (cho test deterministic).
        """
        if not game_key:
            raise ValueError("game_key không được rỗng")
        if not secret_key:
            raise ValueError("secret_key không được rỗng")
        self._game_key = game_key
        self._secret_key = secret_key
        self._environment = environment
        self._base_url = (
            base_url
            if base_url is not None
            else game_config.GA_BASE_URLS.get(environment)
        )
        if not self._base_url:
            raise ValueError(
                f"Environment '{environment}' không có trong "
                f"game_config.GA_BASE_URLS ({list(game_config.GA_BASE_URLS)})"
            )
        self._base_url = self._base_url.rstrip("/")
        self._timeout = timeout
        self._user_agent = user_agent
        if http_session is None:
            # Lazy import requests
            self._session = _get_requests().Session()
        else:
            self._session = http_session
        self._time_source = time_source or time.time
        self._id_generator = id_generator or self._default_id_generator

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def game_key(self) -> str:
        return self._game_key

    @property
    def secret_key(self) -> str:
        return self._secret_key

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def base_url(self) -> str:
        return self._base_url

    # ------------------------------------------------------------------
    # Init endpoint
    # ------------------------------------------------------------------
    def init(
        self,
        use_gzip: bool = False,
    ) -> InitResult:
        """Gọi ``POST /v2/<game_key>/init`` để lấy server_ts + tính offset.

        Side effect: set server_ts offset cho ``annotations`` module
        (Bước 6) – tất cả event sau đó dùng ``client_ts + offset``.

        Args:
            use_gzip: True nếu gửi body gzip. Mặc định False vì init
                      body thường rất nhỏ (empty hoặc SDK info).

        Returns:
            InitResult với server_ts, local_ts, offset, configs_hash.
        """
        endpoint = DEFAULT_INIT_PATH.format(game_key=self._game_key)
        url = f"{self._base_url}{endpoint}"

        # Body: theo GameAnalytics, init body có thể là empty object {}
        # hoặc JSON mô tả SDK. Ở đây gửi object rỗng.
        body_dict: dict[str, Any] = {}
        local_ts = int(self._time_source())

        if use_gzip:
            body_bytes, headers = self._build_gzip_request(body_dict)
        else:
            body_bytes, headers = self._build_json_request(body_dict)

        headers["User-Agent"] = self._user_agent

        # Log request
        req_log = RequestLog(
            request_kind="init",
            endpoint=endpoint,
            payload_size=len(body_bytes),
            timestamp=local_ts,
            event_count=0,
            retry_count=0,
        )

        # POST
        response, req_log = self._do_post(
            url=url,
            body=body_bytes,
            headers=headers,
            req_log=req_log,
        )

        # Parse response
        try:
            data = self._parse_json_response(response)
        except GameAnalyticsError as e:
            req_log.error = str(e)
            raise

        # Lấy server_ts (GameAnalytics trả "server_ts" hoặc "timestamp")
        server_ts = self._extract_server_ts(data)
        if server_ts is None:
            error_msg = f"Response init không chứa server_ts: {data}"
            req_log.error = error_msg
            raise GameAnalyticsError(
                error_msg,
                status_code=response.status_code,
                body=response.text,
                request_log=req_log,
            )

        # Tính offset
        offset = server_ts - local_ts

        # Side effect: set global offset
        self._set_global_offset(offset)

        configs_hash = data.get("configs_hash")

        req_log.status_code = response.status_code
        logger.info(
            "GA init OK: server_ts=%d local_ts=%d offset=%+ds",
            server_ts, local_ts, offset,
        )

        return InitResult(
            server_ts=server_ts,
            local_ts=local_ts,
            offset=offset,
            configs_hash=configs_hash,
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Events endpoint
    # ------------------------------------------------------------------
    def send_events(
        self,
        events: list[dict[str, Any]],
        batch_id: str | None = None,
        use_gzip: bool = True,
        retry_count: int = 0,
    ) -> SendResult:
        """Gọi ``POST /v2/<game_key>/events`` để gửi batch.

        Args:
            events: list event dict (PHẢI là list, KHÔNG phải object).
            batch_id: id định danh (auto-generate nếu None).
            use_gzip: True (mặc định) → gzip body + Authorization trên gzip.
            retry_count: số lần retry (do caller quản lý, không tự retry).

        Returns:
            SendResult với success, status_code, batch_id, ...

        Raises:
            GameAnalyticsError: nếu response không phải 200 OK.
        """
        if not isinstance(events, list):
            raise TypeError(
                f"events phải là list (theo INSTRUCTION: "
                f"'Body = JSON list (KHÔNG phải object)'), "
                f"nhận {type(events).__name__}"
            )
        if not events:
            raise ValueError("events rỗng – không gửi batch")

        endpoint = DEFAULT_EVENTS_PATH.format(game_key=self._game_key)
        url = f"{self._base_url}{endpoint}"

        # Build body + auth
        if use_gzip:
            body_bytes, headers = self._build_gzip_request(events)
        else:
            body_bytes, headers = self._build_json_request(events)
        headers["User-Agent"] = self._user_agent

        # Log
        _batch_id = batch_id or self._id_generator()
        ts = int(self._time_source())
        req_log = RequestLog(
            request_kind="events",
            endpoint=endpoint,
            payload_size=len(body_bytes),
            timestamp=ts,
            event_count=len(events),
            batch_id=_batch_id,
            retry_count=retry_count,
        )

        # POST
        response, req_log = self._do_post(
            url=url,
            body=body_bytes,
            headers=headers,
            req_log=req_log,
        )

        req_log.status_code = response.status_code
        success = response.status_code == GA_OK

        if not success:
            error_msg = (
                f"GA send_events failed: status={response.status_code} "
                f"body={response.text[:300]!r}"
            )
            req_log.error = error_msg
            err = self._make_error(
                response.status_code,
                error_msg,
                response.text,
                req_log,
            )
            logger.error(error_msg)
            raise err

        logger.info(
            "GA send_events OK: batch=%s events=%d size=%dB status=%d retry=%d",
            _batch_id, len(events), len(body_bytes),
            response.status_code, retry_count,
        )

        return SendResult(
            success=True,
            status_code=response.status_code,
            batch_id=_batch_id,
            event_count=len(events),
            payload_size=len(body_bytes),
            retry_count=retry_count,
            timestamp=ts,
            request_log=req_log,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_json_request(
        self,
        body_dict_or_list: Any,
    ) -> tuple[bytes, dict[str, str]]:
        """Build body = JSON bytes + headers (raw JSON, no gzip)."""
        from .auth import build_headers
        from .compressor import serialize_events
        # Body = JSON bytes
        if isinstance(body_dict_or_list, list):
            body_bytes = serialize_events(body_dict_or_list)
        else:
            body_bytes = json.dumps(
                body_dict_or_list, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
        # Headers + Auth
        headers = build_headers(
            body_bytes, self._secret_key, use_gzip=False,
        )
        return body_bytes, headers

    def _build_gzip_request(
        self,
        body_dict_or_list: Any,
    ) -> tuple[bytes, dict[str, str]]:
        """Build body = gzip bytes + headers (gzip mode)."""
        from .auth import build_headers
        from .compressor import compress_events, serialize_events
        if isinstance(body_dict_or_list, list):
            body_bytes = compress_events(body_dict_or_list)
        else:
            # dict → JSON → gzip
            raw = json.dumps(
                body_dict_or_list, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            import gzip as _gzip
            body_bytes = _gzip.compress(raw)
        headers = build_headers(
            body_bytes, self._secret_key, use_gzip=True,
        )
        return body_bytes, headers

    def _do_post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        req_log: RequestLog,
    ) -> tuple[Any, RequestLog]:
        """POST request. Returns (response, updated req_log)."""
        start = self._time_source()
        try:
            response = self._session.post(
                url,
                data=body,
                headers=headers,
                timeout=self._timeout,
            )
        except _get_requests().exceptions.Timeout as e:
            duration_ms = (self._time_source() - start) * 1000
            req_log.duration_ms = duration_ms
            req_log.error = f"timeout: {e}"
            logger.error("GA request timeout: %s", url)
            raise GameAnalyticsError(
                f"Request timeout sau {self._timeout}s: {url}",
                request_log=req_log,
            ) from e
        except _get_requests().exceptions.RequestException as e:
            duration_ms = (self._time_source() - start) * 1000
            req_log.duration_ms = duration_ms
            req_log.error = f"network: {e}"
            logger.error("GA request network error: %s | %s", url, e)
            raise GameAnalyticsError(
                f"Network error: {e}",
                request_log=req_log,
            ) from e

        duration_ms = (self._time_source() - start) * 1000
        req_log.duration_ms = duration_ms
        return response, req_log

    def _parse_json_response(self, response: Any) -> dict[str, Any]:
        """Parse JSON response, raise GameAnalyticsError nếu lỗi."""
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as e:
            raise GameAnalyticsError(
                f"Response không phải JSON: {e}",
                status_code=response.status_code,
                body=response.text[:500],
            ) from e
        if not isinstance(data, dict):
            raise GameAnalyticsError(
                f"Response JSON phải là object, nhận {type(data).__name__}",
                status_code=response.status_code,
                body=response.text[:500],
            )
        return data

    def _extract_server_ts(self, data: dict[str, Any]) -> int | None:
        """Lấy server_ts từ response init.

        GameAnalytics trả ``server_ts`` (int, seconds since epoch).
        Nếu không có thì trả None.
        """
        ts = data.get("server_ts") or data.get("timestamp")
        if ts is None:
            return None
        try:
            return int(ts)
        except (TypeError, ValueError):
            return None

    def _set_global_offset(self, offset: int) -> None:
        """Set global server_ts offset (Bước 6 annotation)."""
        try:
            from .annotations import set_server_ts_offset
            set_server_ts_offset(offset)
        except ImportError:
            logger.warning(
                "annotations module không khả dụng – "
                "offset KHÔNG được set global. offset=%d", offset,
            )

    def _make_error(
        self,
        status_code: int,
        message: str,
        body: str,
        req_log: RequestLog,
    ) -> GameAnalyticsError:
        """Chọn exception class phù hợp theo status_code."""
        if status_code in (GA_UNAUTHORIZED, GA_FORBIDDEN):
            return GameAnalyticsAuthError(
                message, status_code, body, req_log,
            )
        if status_code == GA_PAYLOAD_TOO_LARGE:
            return GameAnalyticsPayloadTooLargeError(
                message, status_code, body, req_log,
            )
        if GA_SERVER_ERROR_MIN <= status_code <= GA_SERVER_ERROR_MAX:
            return GameAnalyticsServerError(
                message, status_code, body, req_log,
            )
        return GameAnalyticsError(
            message, status_code, body, req_log,
        )

    @staticmethod
    def _default_id_generator() -> str:
        """Batch ID mặc định: ``batch_<timestamp>_<rand>``."""
        ts = int(time.time() * 1000)
        rand = os.urandom(3).hex()
        return f"batch_{ts}_{rand}"

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        env_path: str | None = None,
        **overrides: Any,
    ) -> "GameAnalyticsClient":
        """Khởi tạo client từ .env file (Bước 1, INSTRUCTION §1.2).

        Đọc GAME_KEY, SECRET_KEY, GA_ENV từ:
            1. Biến môi trường process (ưu tiên cao)
            2. File .env (tìm tự động hoặc theo env_path)

        Args:
            env_path: đường dẫn .env. None = tự tìm.
            **overrides: ghi đè argument (vd: timeout, base_url).

        Returns:
            GameAnalyticsClient đã inject credentials.

        Raises:
            ValueError: nếu thiếu GAME_KEY hoặc SECRET_KEY.
            FileNotFoundError: nếu không tìm thấy .env và env var rỗng.

        Example::

            client = GameAnalyticsClient.from_env()
            client.init()
            client.send_events(events)
        """
        # Lazy import credentials (tránh circular)
        from config.credentials import get_credentials
        creds = get_credentials(env_path=env_path)
        return cls(
            game_key=creds.game_key,
            secret_key=creds.secret_key,
            environment=creds.environment,
            **overrides,
        )


# =========================================================================
# Convenience: singleton-ish default client (optional)
# =========================================================================

_default_client: GameAnalyticsClient | None = None


def get_default_client() -> GameAnalyticsClient | None:
    """Trả default client (None nếu chưa init)."""
    return _default_client


def set_default_client(client: GameAnalyticsClient | None) -> None:
    """Set default client (None để clear)."""
    global _default_client
    _default_client = client


__all__ = [
    "DEFAULT_INIT_PATH",
    "DEFAULT_EVENTS_PATH",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_USER_AGENT",
    "GA_OK",
    "GameAnalyticsAuthError",
    "GameAnalyticsClient",
    "GameAnalyticsError",
    "GameAnalyticsPayloadTooLargeError",
    "GameAnalyticsServerError",
    "InitResult",
    "RequestLog",
    "SendResult",
    "get_default_client",
    "set_default_client",
]
