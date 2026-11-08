"""GZIP Compression – nén payload trước khi gửi GameAnalytics.

Theo BƯỚC 18 INSTRUCTION.md:

Pipeline đúng::

    events
      ↓
    JSON serialization
      ↓
    gzip compression
      ↓
    HMAC trên compressed body    ← quan trọng: HMAC trên GZIP DATA,
                                    không phải JSON
      ↓
    HTTP POST

Header::

    Content-Type: application/json
    Content-Encoding: gzip

GameAnalytics yêu cầu gửi payload đã được gzip nén. Server sẽ tự
decompress trước khi parse.

Quy tắc bắt buộc:

    - Body thật gửi qua HTTP = ``gzip.compress(json.dumps(events))``
    - HMAC phải tính trên gzip bytes (Bước 19), KHÔNG phải JSON gốc.
    - ``Content-Encoding: gzip`` PHẢI có trong header để server biết.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from typing import Any

# Default compression level (1-9). 6 = default của Python, cân bằng
# tốc độ vs tỉ số nén. 9 = nén tối đa nhưng chậm.
DEFAULT_COMPRESS_LEVEL: int = 6

# Gzip magic header: 2 bytes đầu tiên của gzip stream
GZIP_MAGIC: bytes = b"\x1f\x8b"

# Headers chuẩn cho HTTP POST lên GameAnalytics
CONTENT_TYPE_GZIP: str = "application/json"
CONTENT_ENCODING_GZIP: str = "gzip"
USER_AGENT: str = "GAP-Simulator/1.0 (GameAnalytics Collection API v2)"


# =========================================================================
# CompressedRequest dataclass
# =========================================================================

@dataclass
class CompressedRequest:
    """Một request đã sẵn sàng gửi qua HTTP.

    Attributes:
        body: gzip bytes của JSON list events.
        raw_size: kích thước JSON trước khi gzip (bytes).
        compressed_size: kích thước sau gzip (bytes).
        headers: dict header cho HTTP POST.
        events: list event gốc (giữ reference cho debug/log).
    """

    body: bytes
    raw_size: int
    compressed_size: int
    headers: dict[str, str]
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def content_length(self) -> int:
        """Kích thước body (= compressed_size)."""
        return self.compressed_size

    @property
    def compression_ratio(self) -> float:
        """Tỉ số nén (compressed / raw). Càng nhỏ càng tốt.

        Returns:
            float ∈ (0, 1]. 1.0 = không nén được.
        """
        if self.raw_size == 0:
            return 1.0
        return self.compressed_size / self.raw_size

    @property
    def event_count(self) -> int:
        """Số event trong batch."""
        return len(self.events)


# =========================================================================
# Core API
# =========================================================================

def serialize_events(
    events: list[dict[str, Any]],
    ensure_ascii: bool = False,
) -> bytes:
    """Serialize list event thành JSON bytes (CHƯA gzip).

    Dùng cho HMAC khi không dùng gzip, hoặc cho debug/log.

    Args:
        events: list event dict.
        ensure_ascii: False (mặc định) để giữ ký tự UTF-8 nhỏ gọn.

    Returns:
        bytes (UTF-8) của JSON array.
    """
    return json.dumps(
        events,
        ensure_ascii=ensure_ascii,
        separators=(",", ":"),  # không thêm whitespace → nhỏ nhất
    ).encode("utf-8")


def compress_events(
    events: list[dict[str, Any]],
    compresslevel: int = DEFAULT_COMPRESS_LEVEL,
) -> bytes:
    """Serialize JSON → gzip compress.

    Args:
        events: list event dict.
        compresslevel: 1 (nhanh, nén kém) – 9 (chậm, nén tốt).
                       Mặc định 6 (cân bằng).

    Returns:
        bytes đã gzip nén. Đây chính là body sẽ gửi qua HTTP POST.
    """
    if compresslevel < 1 or compresslevel > 9:
        raise ValueError(
            f"compresslevel phải ∈ [1, 9], nhận {compresslevel}"
        )
    raw = serialize_events(events)
    return gzip.compress(raw, compresslevel=compresslevel)


def decompress_events(body: bytes) -> list[dict[str, Any]]:
    """Decompress gzip body → list event.

    Hàm reverse của ``compress_events``. Dùng cho debug, test, hoặc
    verify response payload.

    Args:
        body: gzip bytes (đầu vào phải bắt đầu bằng GZIP_MAGIC).

    Returns:
        list event dict đã parse.

    Raises:
        ValueError: nếu body không phải gzip hợp lệ.
    """
    if not is_gzip(body):
        raise ValueError(
            "Body không phải gzip hợp lệ. "
            f"Thiếu magic header {GZIP_MAGIC.hex()}."
        )
    raw = gzip.decompress(body)
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, list):
        raise ValueError(
            f"Decompressed JSON phải là list, nhận {type(parsed).__name__}"
        )
    return parsed


def is_gzip(data: bytes) -> bool:
    """Kiểm tra data có phải gzip stream không (check magic header).

    Args:
        data: bytes bất kỳ.

    Returns:
        True nếu 2 byte đầu là GZIP_MAGIC (1f 8b).
    """
    return len(data) >= 2 and data[:2] == GZIP_MAGIC


# =========================================================================
# High-level: build full request
# =========================================================================

def make_compressed_request(
    events: list[dict[str, Any]],
    compresslevel: int = DEFAULT_COMPRESS_LEVEL,
    extra_headers: dict[str, str] | None = None,
) -> CompressedRequest:
    """Tạo CompressedRequest sẵn sàng gửi qua HTTP.

    Args:
        events: list event dict.
        compresslevel: gzip level (1-9), mặc định 6.
        extra_headers: header bổ sung (Authorization, sdk_version, ...).
                       Sẽ được merge vào headers mặc định.

    Returns:
        CompressedRequest với body gzip + headers chuẩn GameAnalytics.
    """
    raw_bytes = serialize_events(events)
    body = gzip.compress(raw_bytes, compresslevel=compresslevel)

    headers = {
        "Content-Type": CONTENT_TYPE_GZIP,
        "Content-Encoding": CONTENT_ENCODING_GZIP,
        "Content-Length": str(len(body)),
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    return CompressedRequest(
        body=body,
        raw_size=len(raw_bytes),
        compressed_size=len(body),
        headers=headers,
        events=events,
    )


def estimate_compressed_size(
    events: list[dict[str, Any]],
) -> int:
    """Ước lượng compressed size mà KHÔNG thực sự nén.

    Hữu ích khi cần check trước khi commit một batch lớn vào
    bộ nhớ. Tính theo heuristic:

        compressed ≈ raw * 0.30 (cho JSON text thông thường)

    Hoặc chính xác hơn: dùng ``compress_events`` để tính.

    Args:
        events: list event dict.

    Returns:
        Ước lượng size gzip (bytes). Dùng cho quick check.
    """
    raw = serialize_events(events)
    # JSON thường nén được ~3-5x với gzip
    return int(len(raw) * 0.30)


# =========================================================================
# Sanity: round-trip test cho smoke test
# =========================================================================

def compress_for_hmac(
    events: list[dict[str, Any]],
    compresslevel: int = DEFAULT_COMPRESS_LEVEL,
) -> bytes:
    """Gzip body DÙNG RIÊNG cho HMAC authentication (Bước 19).

    Hàm này về cơ bản giống ``compress_events``, nhưng đặt tên rõ
    ràng để code gọi HMAC trong Bước 19 đọc rõ ý đồ:

        hmac_input = compress_for_hmac(events)
        auth = HMAC_SHA256(hmac_input, SECRET_KEY)

    Returns:
        gzip bytes (giống compress_events).
    """
    return compress_events(events, compresslevel=compresslevel)


__all__ = [
    "CompressedRequest",
    "CONTENT_TYPE_GZIP",
    "CONTENT_ENCODING_GZIP",
    "DEFAULT_COMPRESS_LEVEL",
    "GZIP_MAGIC",
    "USER_AGENT",
    "compress_events",
    "compress_for_hmac",
    "decompress_events",
    "estimate_compressed_size",
    "is_gzip",
    "make_compressed_request",
    "serialize_events",
]
