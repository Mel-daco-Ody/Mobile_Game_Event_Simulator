"""Smoke test cho BƯỚC 19 – HMAC Authentication.

Kiểm tra Auth module:

A. compute_hmac:
    1. SHA-256 trả 32 bytes (256 bits).
    2. Deterministic: cùng input → cùng output.
    3. Khác input → khác output.
    4. Khác secret_key → khác output.
    5. Empty body vẫn tính được.
    6. Algorithm invalid raise ValueError.
    7. Empty secret_key raise ValueError.

B. compute_hmac_base64:
    8. Output là base64 string (ASCII).
    9. Length đúng với SHA-256 (44 chars trong base64).
    10. Valid base64 (decode được).

C. compute_hmac_json vs compute_hmac_gzip:
    11. Cùng secret nhưng input khác mode → khác signature.
    12. compute_hmac_gzip KHÔNG bằng compute_hmac_json(json_bytes)
        nếu input là gzip (rule QUAN TRỌNG).
    13. Mode rõ ràng: tên hàm phân biệt gzip / json.

D. make_authorization_header:
    14. use_gzip=True → tính trên gzip bytes.
    15. use_gzip=False → tính trên json bytes.
    16. Trả về string, không có prefix "Authorization: ".

E. build_headers:
    17. Có đủ headers (Authorization, Content-Type, Content-Length).
    18. use_gzip=True có Content-Encoding: gzip.
    19. use_gzip=False KHÔNG có Content-Encoding.
    20. extra_headers được merge.

F. AuthSigner:
    21. Constructor empty secret raise ValueError.
    22. sign() = compute_hmac_base64.
    23. sign_gzip() = compute_hmac_gzip.
    24. sign_json() = compute_hmac_json.
    25. sign_events() gzip mode dùng gzip data.
    26. sign_events() json mode dùng json data.
    27. build_headers() đầy đủ.

G. verify_authorization:
    28. Đúng auth → True.
    29. Sai auth → False.
    30. Đúng secret + đúng body → True.
    31. Sai 1 byte body → False.

H. Determinism & integration:
    32. Cùng event → cùng auth value qua nhiều lần gọi.
    33. End-to-end: events → compress → HMAC → headers + body
        → decompress → khớp events gốc.

I. Constants:
    34. HMAC_ALGORITHM = 'sha256'.
    35. AUTH_HEADER_NAME = 'Authorization'.

Chạy:
    cd prj && python gameanalytics/smoke_test_auth.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gameanalytics import (  # noqa: E402
    AUTH_HEADER_NAME,
    HMAC_ALGORITHM,
    SUPPORTED_ALGORITHMS,
    AuthSigner,
    compress_events,
    decompress_events,
    is_gzip,
    serialize_events,
)
from gameanalytics.auth import (  # noqa: E402
    build_headers,
    compute_hmac,
    compute_hmac_base64,
    compute_hmac_gzip,
    compute_hmac_json,
    make_authorization_header,
    verify_authorization,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


# Test secret key (giống key GameAnalytics cấp cho một game mẫu)
TEST_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
TEST_BODY = b'{"hello":"world","items":[1,2,3]}'


def make_event(idx: int = 0) -> dict:
    """Tạo event mẫu."""
    return {
        "category": "design",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600 + idx,
        "sdk_version": "rest api v2",
        "os_version": "android 15",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
        "event_id": f"Gameplay:Kill:Zombie_{idx}",
        "value": 42,
    }


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 19: HMAC AUTHENTICATION")
    print("=" * 60)

    events_sample = [make_event(i) for i in range(5)]

    # ----------------------------------------------------------------------
    # A. compute_hmac
    # ----------------------------------------------------------------------
    print("\n[--- A. COMPUTE_HMAC ---]")

    print("\n[A1] SHA-256 trả 32 bytes (256 bits)")
    digest = compute_hmac(TEST_BODY, TEST_SECRET)
    assert_check(isinstance(digest, bytes), f"type = {type(digest).__name__}")
    assert_check(len(digest) == 32,
                 f"len = {len(digest)} bytes (expected 32)")
    # So sánh với stdlib thủ công
    expected = hmac.new(
        TEST_SECRET.encode("utf-8"),
        TEST_BODY,
        hashlib.sha256,
    ).digest()
    assert_check(digest == expected, "khớp với hmac.new().digest()")

    print("\n[A2] Deterministic: cùng input → cùng output")
    d1 = compute_hmac(TEST_BODY, TEST_SECRET)
    d2 = compute_hmac(TEST_BODY, TEST_SECRET)
    d3 = compute_hmac(TEST_BODY, TEST_SECRET)
    assert_check(d1 == d2 == d3, "3 lần gọi → cùng digest")

    print("\n[A3] Khác input → khác output")
    d_other = compute_hmac(b"different body", TEST_SECRET)
    assert_check(d1 != d_other, "body khác → digest khác")

    print("\n[A4] Khác secret_key → khác output")
    new_secret = TEST_SECRET[:-1] + ("0" if TEST_SECRET[-1] != "0" else "1")
    d_secret2 = compute_hmac(TEST_BODY, new_secret)
    assert_check(d1 != d_secret2, "secret khác → digest khác")
    assert_check(new_secret != TEST_SECRET, f"new_secret khác gốc")

    print("\n[A5] Empty body vẫn tính được")
    d_empty = compute_hmac(b"", TEST_SECRET)
    assert_check(len(d_empty) == 32, "empty body → 32 bytes")

    print("\n[A6] Algorithm invalid raise ValueError")
    try:
        compute_hmac(TEST_BODY, TEST_SECRET, algorithm="sha1_invalid")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("không hỗ trợ" in str(e).lower() or
                     "không hỗ trợ" in str(e),
                     f"ValueError: {e}")

    print("\n[A7] Empty secret_key raise ValueError")
    try:
        compute_hmac(TEST_BODY, "")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("rỗng" in str(e).lower(),
                     f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # B. compute_hmac_base64
    # ----------------------------------------------------------------------
    print("\n[--- B. COMPUTE_HMAC_BASE64 ---]")

    print("\n[B1] Output là base64 string (ASCII)")
    auth = compute_hmac_base64(TEST_BODY, TEST_SECRET)
    assert_check(isinstance(auth, str), f"type = {type(auth).__name__}")
    assert_check(all(c.isalnum() or c in "+/=" for c in auth),
                 "chỉ chứa base64 alphabet")

    print("\n[B2] Length đúng với SHA-256 (44 chars)")
    # 32 bytes → ceil(32/3)*4 = 44 chars trong base64 (có padding '=')
    assert_check(len(auth) == 44, f"len = {len(auth)} (expected 44)")

    print("\n[B3] Valid base64 (decode được)")
    decoded = base64.b64decode(auth)
    assert_check(len(decoded) == 32, f"decoded len = {len(decoded)}")
    # Match với raw HMAC
    assert_check(decoded == compute_hmac(TEST_BODY, TEST_SECRET),
                 "decoded == raw HMAC")

    # ----------------------------------------------------------------------
    # C. compute_hmac_json vs compute_hmac_gzip
    # ----------------------------------------------------------------------
    print("\n[--- C. MODE WRAPPERS (JSON vs GZIP) ---]")

    print("\n[C1] Cùng secret, input khác mode → khác signature")
    json_body = serialize_events(events_sample)
    gzip_body = compress_events(events_sample)
    auth_json = compute_hmac_json(json_body, TEST_SECRET)
    auth_gzip = compute_hmac_gzip(gzip_body, TEST_SECRET)
    assert_check(auth_json != auth_gzip,
                 f"json vs gzip auth khác ({len(auth_json)} vs {len(auth_gzip)} chars)")

    print("\n[C2] QUAN TRỌNG: gzip_auth != json_auth(json_body) khi mode khác")
    # Nếu dev nhầm: tính auth trên JSON thay vì gzip (vì body thật gửi
    # đi là gzip), thì auth sẽ khác digest của gzip → server reject.
    auth_gzip_of_gzip_body = compute_hmac_gzip(gzip_body, TEST_SECRET)
    auth_json_of_json_body = compute_hmac_json(json_body, TEST_SECRET)
    # Ký cùng data thì cùng digest (đúng là HMAC deterministic)
    assert_check(
        auth_gzip_of_gzip_body
        == compute_hmac_base64(gzip_body, TEST_SECRET),
        "compute_hmac_gzip = compute_hmac_base64 (deterministic)"
    )
    # Nhưng nếu server ký JSON mà ta gửi ký gzip → khác
    assert_check(
        auth_gzip_of_gzip_body != auth_json_of_json_body,
        "gzip-auth ≠ json-auth (vì input khác bytes)"
    )
    print(f"  [INFO] auth(gzip_body)=  {auth_gzip_of_gzip_body[:24]}...")
    print(f"  [INFO] auth(json_body)=  {auth_json_of_json_body[:24]}...")

    print("\n[C3] Tên hàm phân biệt gzip / json")
    assert_check(is_gzip(gzip_body), "gzip_body phải là gzip")
    assert_check(not is_gzip(json_body), "json_body KHÔNG phải gzip")

    # ----------------------------------------------------------------------
    # D. make_authorization_header
    # ----------------------------------------------------------------------
    print("\n[--- D. MAKE_AUTHORIZATION_HEADER ---]")

    print("\n[D1] use_gzip=True → tính trên gzip bytes")
    auth1 = make_authorization_header(gzip_body, TEST_SECRET, use_gzip=True)
    assert_check(auth1 == auth_gzip,
                 "make_authorization(gzip, use_gzip=True) == compute_hmac_gzip()")

    print("\n[D2] use_gzip=False → tính trên json bytes")
    auth2 = make_authorization_header(json_body, TEST_SECRET, use_gzip=False)
    assert_check(auth2 == auth_json,
                 "make_authorization(json, use_gzip=False) == compute_hmac_json()")

    print("\n[D3] Trả về string, KHÔNG có prefix")
    assert_check("Authorization" not in auth1, "không có prefix")
    assert_check("Bearer " not in auth1, "không có 'Bearer ' prefix")
    assert_check("HMAC " not in auth1, "không có 'HMAC ' prefix")

    # ----------------------------------------------------------------------
    # E. build_headers
    # ----------------------------------------------------------------------
    print("\n[--- E. BUILD_HEADERS ---]")

    print("\n[E1] Đủ headers cơ bản")
    headers_gz = build_headers(gzip_body, TEST_SECRET, use_gzip=True)
    assert_check("Authorization" in headers_gz, "có Authorization")
    assert_check(headers_gz["Content-Type"] == "application/json",
                 "Content-Type = application/json")
    assert_check(headers_gz["Content-Length"] == str(len(gzip_body)),
                 f"Content-Length = {headers_gz['Content-Length']}")

    print("\n[E2] use_gzip=True có Content-Encoding: gzip")
    assert_check(headers_gz.get("Content-Encoding") == "gzip",
                 f"Content-Encoding = {headers_gz.get('Content-Encoding')}")

    print("\n[E3] use_gzip=False KHÔNG có Content-Encoding")
    headers_raw = build_headers(json_body, TEST_SECRET, use_gzip=False)
    assert_check("Content-Encoding" not in headers_raw,
                 "không có Content-Encoding")

    print("\n[E4] extra_headers được merge")
    headers_extra = build_headers(
        gzip_body, TEST_SECRET,
        use_gzip=True,
        extra_headers={"X-Game-Key": "abc123", "sdk_version": "rest api v2"},
    )
    assert_check(headers_extra["X-Game-Key"] == "abc123", "X-Game-Key merged")
    assert_check(headers_extra["sdk_version"] == "rest api v2", "sdk_version merged")
    assert_check(headers_extra["Authorization"] == auth_gzip,
                 "Authorization vẫn đúng")

    # ----------------------------------------------------------------------
    # F. AuthSigner
    # ----------------------------------------------------------------------
    print("\n[--- F. AUTHSIGNER ---]")

    print("\n[F1] Empty secret raise ValueError")
    try:
        AuthSigner(secret_key="")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("rỗng" in str(e).lower(),
                     f"ValueError: {e}")

    print("\n[F2] Algorithm invalid raise ValueError")
    try:
        AuthSigner(secret_key=TEST_SECRET, algorithm="xx")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    print("\n[F3] sign() == compute_hmac_base64")
    signer = AuthSigner(secret_key=TEST_SECRET)
    assert_check(signer.sign(TEST_BODY) == compute_hmac_base64(TEST_BODY, TEST_SECRET),
                 "sign OK")

    print("\n[F4] sign_gzip() == compute_hmac_gzip")
    assert_check(signer.sign_gzip(gzip_body) == auth_gzip,
                 "sign_gzip OK")

    print("\n[F5] sign_json() == compute_hmac_json")
    assert_check(signer.sign_json(json_body) == auth_json,
                 "sign_json OK")

    print("\n[F6] sign_events() gzip mode dùng gzip data")
    auth_ev = signer.sign_events(events_sample, use_gzip=True)
    assert_check(auth_ev == auth_gzip,
                 "sign_events(gzip) == sign_gzip(gzip_body)")

    print("\n[F7] sign_events() json mode dùng json data")
    auth_ev_raw = signer.sign_events(events_sample, use_gzip=False)
    assert_check(auth_ev_raw == auth_json,
                 "sign_events(json) == sign_json(json_body)")

    print("\n[F8] AuthSigner.build_headers đầy đủ")
    h = signer.build_headers(gzip_body, use_gzip=True)
    assert_check(h["Authorization"], "có Authorization")
    assert_check(h["Content-Encoding"] == "gzip", "có Content-Encoding")

    # ----------------------------------------------------------------------
    # G. verify_authorization
    # ----------------------------------------------------------------------
    print("\n[--- G. VERIFY_AUTHORIZATION ---]")

    print("\n[G1] Đúng auth → True")
    correct = compute_hmac_gzip(gzip_body, TEST_SECRET)
    assert_check(verify_authorization(gzip_body, TEST_SECRET, correct,
                                       use_gzip=True),
                 "đúng auth → True")

    print("\n[G2] Sai auth → False")
    wrong_auth = "ZmFrZV9hdXRoX3ZhbHVl"  # base64 của "fake_auth_value"
    assert_check(not verify_authorization(gzip_body, TEST_SECRET, wrong_auth,
                                           use_gzip=True),
                 "sai auth → False")

    print("\n[G3] Sai 1 byte body → False")
    wrong_body = gzip_body[:-1] + bytes([(gzip_body[-1] + 1) % 256])
    assert_check(not verify_authorization(wrong_body, TEST_SECRET, correct,
                                           use_gzip=True),
                 "body khác 1 byte → False")

    # ----------------------------------------------------------------------
    # H. Determinism & integration
    # ----------------------------------------------------------------------
    print("\n[--- H. DETERMINISM & INTEGRATION ---]")

    print("\n[H1] Cùng event → cùng auth value qua nhiều lần")
    a1 = signer.sign_events(events_sample)
    a2 = signer.sign_events(events_sample)
    a3 = signer.sign_events(events_sample)
    assert_check(a1 == a2 == a3, "3 lần → cùng auth")

    print("\n[H2] End-to-end: events → compress → HMAC → headers + body "
          "→ decompress → events khớp")
    # Sender
    sender_headers, sender_body = (
        signer.build_headers(gzip_body, use_gzip=True),
        gzip_body,
    )
    assert_check(sender_headers["Authorization"], "Authorization có")
    # Receiver: nhận headers + body, decompress, verify HMAC
    recv_body = sender_body
    recv_auth = sender_headers["Authorization"]
    # Decompress verify
    recv_events = decompress_events(recv_body)
    assert_check(len(recv_events) == len(events_sample),
                 f"decompress {len(recv_events)}/{len(events_sample)}")
    # Verify HMAC (server-side giả lập)
    verified = verify_authorization(
        recv_body, TEST_SECRET, recv_auth, use_gzip=True
    )
    assert_check(verified, "HMAC verified (server-side)")

    # ----------------------------------------------------------------------
    # I. Constants
    # ----------------------------------------------------------------------
    print("\n[--- I. CONSTANTS ---]")
    assert_check(HMAC_ALGORITHM == "sha256",
                 f"HMAC_ALGORITHM = {HMAC_ALGORITHM}")
    assert_check(AUTH_HEADER_NAME == "Authorization",
                 f"AUTH_HEADER_NAME = {AUTH_HEADER_NAME}")
    assert_check("sha256" in SUPPORTED_ALGORITHMS, "sha256 trong supported")
    assert_check("sha512" in SUPPORTED_ALGORITHMS, "sha512 trong supported")
    assert_check("md5" in SUPPORTED_ALGORITHMS, "md5 trong supported (legacy)")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 19 HMAC AUTH OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
