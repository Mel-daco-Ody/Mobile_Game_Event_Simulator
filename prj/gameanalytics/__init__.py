"""GameAnalytics package – gửi events lên GameAnalytics Collection API v2.

Hiện tại:
    - annotations: factory tạo common fields cho mọi event (Bước 6)
    - validator:   schema / business / batch validation (Bước 15)
    - batcher:     gom event theo compressed size < 1 MB (Bước 17)
    - compressor:  gzip nén payload + build CompressedRequest (Bước 18)
    - auth:        HMAC-SHA256 + Base64 cho Authorization header (Bước 19)
    - client:      HTTP client (init + send_events) (Bước 20)
    - retry:       retry mechanism với exponential backoff (Bước 21)

Các module khác (raw_writer, ...) sẽ được bổ sung ở Bước 22.
"""

from __future__ import annotations

from .annotations import (
    create_default_annotations,
    get_server_ts_offset,
    set_server_ts_offset,
)
from .auth import (
    AUTH_HEADER_NAME,
    HMAC_ALGORITHM,
    SUPPORTED_ALGORITHMS,
    AuthSigner,
    build_headers,
    compute_hmac,
    compute_hmac_base64,
    compute_hmac_gzip,
    compute_hmac_json,
    make_authorization_header,
    verify_authorization,
)
from .batcher import (
    Batch,
    Batcher,
    BatcherStats,
    batch_events_by_size,
)
from .client import (
    DEFAULT_EVENTS_PATH,
    DEFAULT_INIT_PATH,
    DEFAULT_TIMEOUT_SEC,
    DEFAULT_USER_AGENT,
    GA_OK,
    GameAnalyticsAuthError,
    GameAnalyticsClient,
    GameAnalyticsError,
    GameAnalyticsPayloadTooLargeError,
    GameAnalyticsServerError,
    InitResult,
    RequestLog,
    SendResult,
    get_default_client,
    set_default_client,
)
from .compressor import (
    CONTENT_ENCODING_GZIP,
    CONTENT_TYPE_GZIP,
    CompressedRequest,
    DEFAULT_COMPRESS_LEVEL,
    GZIP_MAGIC,
    USER_AGENT,
    compress_events,
    compress_for_hmac,
    decompress_events,
    estimate_compressed_size,
    is_gzip,
    make_compressed_request,
    serialize_events,
)
from .retry import (
    RetryBatch,
    RetryPolicy,
    RetryQueue,
    RetryQueueStats,
    RetryableGameAnalyticsClient,
    create_retry_client,
)
from .validator import (
    EventValidator,
    OPTIONAL_COMMON_FIELDS,
    REQUIRED_COMMON_FIELDS,
    VALID_CATEGORIES,
    ValidationError,
    ValidationResult,
)

__all__ = [
    # Annotations (Bước 6)
    "create_default_annotations",
    "set_server_ts_offset",
    "get_server_ts_offset",
    # Validator (Bước 15)
    "EventValidator",
    "ValidationError",
    "ValidationResult",
    "REQUIRED_COMMON_FIELDS",
    "OPTIONAL_COMMON_FIELDS",
    "VALID_CATEGORIES",
    # Batcher (Bước 17)
    "Batcher",
    "Batch",
    "BatcherStats",
    "batch_events_by_size",
    # Compressor (Bước 18)
    "CompressedRequest",
    "compress_events",
    "compress_for_hmac",
    "decompress_events",
    "estimate_compressed_size",
    "is_gzip",
    "make_compressed_request",
    "serialize_events",
    "DEFAULT_COMPRESS_LEVEL",
    "GZIP_MAGIC",
    "CONTENT_TYPE_GZIP",
    "CONTENT_ENCODING_GZIP",
    "USER_AGENT",
    # Auth (Bước 19)
    "AuthSigner",
    "compute_hmac",
    "compute_hmac_base64",
    "compute_hmac_gzip",
    "compute_hmac_json",
    "make_authorization_header",
    "build_headers",
    "verify_authorization",
    "AUTH_HEADER_NAME",
    "HMAC_ALGORITHM",
    "SUPPORTED_ALGORITHMS",
    # Client (Bước 20)
    "GameAnalyticsClient",
    "GameAnalyticsError",
    "GameAnalyticsAuthError",
    "GameAnalyticsPayloadTooLargeError",
    "GameAnalyticsServerError",
    "InitResult",
    "RequestLog",
    "SendResult",
    "get_default_client",
    "set_default_client",
    "GA_OK",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_USER_AGENT",
    "DEFAULT_INIT_PATH",
    "DEFAULT_EVENTS_PATH",
    # Retry (Bước 21)
    "RetryPolicy",
    "RetryBatch",
    "RetryQueue",
    "RetryQueueStats",
    "RetryableGameAnalyticsClient",
    "create_retry_client",
    # Credentials (Bước 1 bổ sung)
    "GameAnalyticsCredentials",
    "get_credentials",
    "load_credentials",
    "require_game_key",
    "require_secret_key",
    "get_ga_env",
]
