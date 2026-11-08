"""HMAC Authentication – sinh Authorization header cho GameAnalytics.

Theo BƯỚC 19 INSTRUCTION.md:

Công thức::

    HMAC_SHA256(request_body, SECRET_KEY)  →  Base64  →  Authorization

Hai trường hợp::

    +-----------+-----------------------------------+
    | Mode      | Body dùng để tính HMAC            |
    +===========+===================================+
    | Không gzip| Raw JSON                          |
    +-----------+-----------------------------------+
    | Có gzip   | Gzipped bytes  ← QUAN TRỌNG      |
    +-----------+-----------------------------------+

> KHÔNG được tính HMAC trên JSON rồi mới gzip nếu request body
> thực tế là gzipped data — server sẽ reject.

Header cuối::

    Authorization: <base64_hmac>
    Content-Type: application/json
    Content-Encoding: gzip     (nếu dùng gzip)

Module này implement đầy đủ cả 2 mode. Trong project này chúng ta
dùng mode **gzip** (Bước 17 + 18), nên ``compute_hmac_gzip()`` /
``AuthSigner.sign_gzip()`` là API chính.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

# Default hash algo GameAnalytics spec yêu cầu (SHA-256 → 32 bytes → Base64).
HMAC_ALGORITHM: str = "sha256"

# Algorithm khác được hỗ trợ nếu GameAnalytics mở rộng (không dùng trong
# project này nhưng có sẵn để không phải đợi spec update).
SUPPORTED_ALGORITHMS: frozenset[str] = frozenset({
    "sha256", "sha384", "sha512", "md5",
})

# Prefix chuẩn GameAnalytics cho Authorization header (KHÔNG có prefix
# theo spec — chỉ là base64 của hmac). Hằng số này để document,
# KHÔNG nên ghép vào header.
# Lưu ý: GameAnalytics yêu cầu ``Authorization: <base64>`` (không có
# prefix "HMAC-SHA256 " hay tương tự).
AUTH_HEADER_NAME: str = "Authorization"


# =========================================================================
# Core HMAC functions
# =========================================================================

def compute_hmac(
    body: bytes,
    secret_key: str,
    algorithm: str = HMAC_ALGORITHM,
) -> bytes:
    """Tính HMAC của ``body`` với ``secret_key``.

    Args:
        body: bytes cần ký (JSON gốc HOẶC gzip bytes).
        secret_key: SECRET_KEY GameAnalytics cấp (str).
        algorithm: hash algorithm, mặc định 'sha256'.

    Returns:
        HMAC digest (raw bytes, CHƯA base64).

    Raises:
        ValueError: nếu algorithm không hỗ trợ hoặc secret_key rỗng.
    """
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"algorithm '{algorithm}' không hỗ trợ. "
            f"Chọn 1 trong {sorted(SUPPORTED_ALGORITHMS)}"
        )
    if not secret_key:
        raise ValueError(
            "secret_key không được rỗng. "
            "Lấy SECRET_KEY từ GameAnalytics Game Setup."
        )
    key_bytes = secret_key.encode("utf-8")
    return hmac.new(
        key_bytes,
        body,
        getattr(hashlib, algorithm),
    ).digest()


def compute_hmac_base64(
    body: bytes,
    secret_key: str,
    algorithm: str = HMAC_ALGORITHM,
) -> str:
    """Tính HMAC + Base64 encode → Authorization header value.

    Args:
        body: bytes cần ký (JSON HOẶC gzip).
        secret_key: SECRET_KEY GameAnalytics.
        algorithm: hash algorithm.

    Returns:
        base64 string (ASCII, không có prefix). Đây là giá trị
        đặt vào header ``Authorization``.
    """
    digest = compute_hmac(body, secret_key, algorithm)
    return base64.b64encode(digest).decode("ascii")


# =========================================================================
# Mode-specific wrappers
# =========================================================================

def compute_hmac_json(
    json_bytes: bytes,
    secret_key: str,
) -> str:
    """HMAC cho mode KHÔNG gzip — input là raw JSON bytes.

    Args:
        json_bytes: JSON array đã serialize (``serialize_events()``).
        secret_key: SECRET_KEY.

    Returns:
        base64 string để đặt vào ``Authorization``.
    """
    return compute_hmac_base64(json_bytes, secret_key)


def compute_hmac_gzip(
    gzip_bytes: bytes,
    secret_key: str,
) -> str:
    """HMAC cho mode CÓ gzip — input là gzipped bytes.

    QUAN TRỌNG: HMAC phải tính trên GZIP DATA, không phải JSON gốc.
    Nếu nhầm sẽ bị server reject.

    Args:
        gzip_bytes: gzip bytes (``compress_events()`` hoặc
                    ``compress_for_hmac()``).
        secret_key: SECRET_KEY.

    Returns:
        base64 string để đặt vào ``Authorization``.
    """
    return compute_hmac_base64(gzip_bytes, secret_key)


# =========================================================================
# Authorization header builders
# =========================================================================

def make_authorization_header(
    body: bytes,
    secret_key: str,
    use_gzip: bool = True,
) -> str:
    """Sinh giá trị cho header ``Authorization``.

    Args:
        body: body bytes thực tế sẽ gửi qua HTTP.
        secret_key: SECRET_KEY GameAnalytics.
        use_gzip: True nếu body đã được gzip (mặc định), False nếu raw JSON.

    Returns:
        base64 string (KHÔNG bao gồm prefix "Authorization: ", chỉ value).
    """
    if use_gzip:
        return compute_hmac_gzip(body, secret_key)
    return compute_hmac_json(body, secret_key)


def build_headers(
    body: bytes,
    secret_key: str,
    use_gzip: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build toàn bộ headers (Authorization + Content-* + extra).

    Bao gồm sẵn:
        - Authorization
        - Content-Type: application/json
        - Content-Encoding: gzip  (nếu use_gzip=True)
        - Content-Length: <len(body)>

    Args:
        body: body bytes (gzip hoặc raw JSON).
        secret_key: SECRET_KEY.
        use_gzip: dùng gzip mode.
        extra_headers: header bổ sung (vd: sdk_version).

    Returns:
        dict header sẵn sàng cho HTTP client (requests, urllib, ...).
    """
    headers = {
        AUTH_HEADER_NAME: make_authorization_header(body, secret_key, use_gzip),
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if use_gzip:
        headers["Content-Encoding"] = "gzip"
    if extra_headers:
        headers.update(extra_headers)
    return headers


# =========================================================================
# AuthSigner – high-level wrapper
# =========================================================================

@dataclass
class AuthSigner:
    """High-level wrapper cho HMAC signing.

    Lưu secret_key 1 lần, cung cấp các method tiện ích.

    Usage::

        signer = AuthSigner(secret_key="0123456789abcdef...")

        # Mode gzip (chính của project này)
        auth_value = signer.sign_gzip(gzip_body)

        # Mode raw JSON
        auth_value = signer.sign_json(json_body)

        # Full headers
        headers = signer.build_headers(gzip_body, use_gzip=True)
    """

    secret_key: str
    algorithm: str = HMAC_ALGORITHM

    def __post_init__(self) -> None:
        if not self.secret_key:
            raise ValueError(
                "AuthSigner.secret_key không được rỗng."
            )
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"algorithm '{self.algorithm}' không hỗ trợ. "
                f"Chọn 1 trong {sorted(SUPPORTED_ALGORITHMS)}"
            )

    def sign(self, body: bytes) -> str:
        """Tính HMAC + Base64 trên body (generic)."""
        return compute_hmac_base64(body, self.secret_key, self.algorithm)

    def sign_gzip(self, gzip_body: bytes) -> str:
        """HMAC trên gzip bytes (mode chính của project)."""
        return compute_hmac_gzip(gzip_body, self.secret_key)

    def sign_json(self, json_body: bytes) -> str:
        """HMAC trên raw JSON bytes (mode không gzip)."""
        return compute_hmac_json(json_body, self.secret_key)

    def sign_events(
        self,
        events: list[dict[str, Any]],
        use_gzip: bool = True,
    ) -> str:
        """Tiện ích: serialize → compress (nếu cần) → HMAC.

        Args:
            events: list event dict.
            use_gzip: True → gzip mode, False → raw JSON mode.

        Returns:
            base64 string.
        """
        # Lazy import để tránh circular import
        from .compressor import compress_events, serialize_events
        if use_gzip:
            body = compress_events(events)
        else:
            body = serialize_events(events)
        return (
            compute_hmac_gzip(body, self.secret_key)
            if use_gzip
            else compute_hmac_json(body, self.secret_key)
        )

    def build_headers(
        self,
        body: bytes,
        use_gzip: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build headers đầy đủ (Authorization + Content-*)."""
        return build_headers(
            body, self.secret_key, use_gzip, extra_headers
        )


# =========================================================================
# Diagnostic / verify
# =========================================================================

def verify_authorization(
    body: bytes,
    secret_key: str,
    expected_auth: str,
    use_gzip: bool = True,
) -> bool:
    """Verify Authorization header có đúng không (dùng cho test).

    Args:
        body: body bytes thực tế.
        secret_key: SECRET_KEY.
        expected_auth: giá trị Authorization nhận được.
        use_gzip: mode gzip hay raw.

    Returns:
        True nếu khớp, False nếu sai.
    """
    actual = make_authorization_header(body, secret_key, use_gzip)
    return hmac.compare_digest(actual, expected_auth)


__all__ = [
    "AUTH_HEADER_NAME",
    "HMAC_ALGORITHM",
    "SUPPORTED_ALGORITHMS",
    "AuthSigner",
    "build_headers",
    "compute_hmac",
    "compute_hmac_base64",
    "compute_hmac_gzip",
    "compute_hmac_json",
    "make_authorization_header",
    "verify_authorization",
]
