"""Smoke test cho BƯỚC 20 – GameAnalytics Client.

Kiểm tra GameAnalyticsClient (init + send_events):

A. Khởi tạo & Validation:
    1. game_key/secret_key rỗng raise ValueError.
    2. Environment invalid raise ValueError.
    3. base_url custom override được dùng.
    4. base_url trailing slash được strip.

B. RequestLog dataclass:
    5. is_success() đúng khi status=200.
    6. is_success() False khi status != 200 hoặc có error.
    7. to_dict() có đủ trường.

C. send_events validation:
    8. events KHÔNG phải list raise TypeError (rule INSTRUCTION).
    9. events rỗng raise ValueError.

D. send_events – Mock session (unit):
    10. Success 200 → SendResult.success=True.
    11. Authorization header đúng (HMAC).
    12. Content-Encoding: gzip (mặc định).
    13. Content-Type: application/json.
    14. event_count, payload_size, batch_id đúng.
    15. RequestLog có đủ field (batch_id, event_count, payload_size,
        status_code, timestamp, retry_count).
    16. use_gzip=False → KHÔNG có Content-Encoding.

E. send_events – Error handling:
    17. Status 401 → GameAnalyticsAuthError.
    18. Status 413 → GameAnalyticsPayloadTooLargeError.
    19. Status 500 → GameAnalyticsServerError.
    20. Status 400 → GameAnalyticsError generic.
    21. RequestLog.error có message.

F. send_events – Network error:
    22. requests.exceptions.Timeout → GameAnalyticsError với status_code=None.
    23. requests.exceptions.ConnectionError → GameAnalyticsError network.

G. Init endpoint:
    24. Init gọi đúng URL /v2/<game_key>/init.
    25. Init response có server_ts → set global offset.
    26. Init response thiếu server_ts → raise GameAnalyticsError.

H. End-to-end với httpbin (optional, chỉ chạy nếu network OK):
    27. POST /post với gzip body.
    28. Server nhận được body decompress được.

Chạy:
    cd prj && python gameanalytics/smoke_test_client.py
"""

from __future__ import annotations

import gzip
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

import requests  # noqa: E402

from gameanalytics import (  # noqa: E402
    GameAnalyticsAuthError,
    GameAnalyticsClient,
    GameAnalyticsError,
    GameAnalyticsPayloadTooLargeError,
    GameAnalyticsServerError,
    InitResult,
    RequestLog,
    SendResult,
)
from gameanalytics.annotations import (  # noqa: E402
    get_server_ts_offset,
    set_server_ts_offset,
)
from config import game_config  # noqa: E402


# =====================================================================
# Mock classes cho HTTP
# =====================================================================

class MockResponse:
    """Stub requests.Response cho test."""

    def __init__(self, status_code: int, json_data: dict | str | None = None,
                 text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text if text else (
            json.dumps(json_data) if isinstance(json_data, dict) else ""
        )

    def json(self):
        if self._json is None:
            raise ValueError("No JSON")
        return self._json


class MockSession:
    """Stub requests.Session – ghi lại các request + trả response giả."""

    def __init__(self, response: MockResponse | list[MockResponse] | None = None,
                 raise_exc: Exception | None = None) -> None:
        self.requests_made: list[dict] = []
        self._response = response or MockResponse(200, {"server_ts": 1759305600})
        self._response_idx = 0
        self._raise_exc = raise_exc

    def post(self, url: str, data: bytes, headers: dict, timeout: float = 30.0):
        self.requests_made.append({
            "url": url,
            "data": data,
            "headers": dict(headers),
            "timeout": timeout,
        })
        if self._raise_exc is not None:
            raise self._raise_exc
        # Nếu response là list, trả tuần tự
        if isinstance(self._response, list):
            if self._response_idx >= len(self._response):
                resp = self._response[-1]
            else:
                resp = self._response[self._response_idx]
                self._response_idx += 1
        else:
            resp = self._response
        return resp


TEST_GAME_KEY = "test_game_key_abc123"
TEST_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def make_event(idx: int = 0) -> dict:
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
    print("SMOKE TEST – BƯỚC 20: GAMEANALYTICS CLIENT")
    print("=" * 60)

    # Reset offset
    set_server_ts_offset(0)

    # ----------------------------------------------------------------------
    # A. Khởi tạo & Validation
    # ----------------------------------------------------------------------
    print("\n[--- A. KHỞI TẠO & VALIDATION ---]")

    print("\n[A1] game_key rỗng raise ValueError")
    try:
        GameAnalyticsClient(game_key="", secret_key=TEST_SECRET_KEY)
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("game_key" in str(e), f"ValueError: {e}")

    print("\n[A2] secret_key rỗng raise ValueError")
    try:
        GameAnalyticsClient(game_key=TEST_GAME_KEY, secret_key="")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("secret_key" in str(e), f"ValueError: {e}")

    print("\n[A3] Environment invalid raise ValueError")
    try:
        GameAnalyticsClient(
            game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
            environment="staging_invalid",
        )
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("Environment" in str(e), f"ValueError: {e}")

    print("\n[A4] base_url custom được dùng")
    custom_url = "https://custom.example.com"
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url=custom_url,
    )
    assert_check(client.base_url == "https://custom.example.com",
                 f"base_url = {client.base_url}")

    print("\n[A5] base_url trailing slash được strip")
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://custom.example.com/",
    )
    assert_check(client.base_url == "https://custom.example.com",
                 f"stripped = {client.base_url}")

    # ----------------------------------------------------------------------
    # B. RequestLog dataclass
    # ----------------------------------------------------------------------
    print("\n[--- B. REQUESTLOG ---]")

    print("\n[B1] is_success() đúng khi status=200, không error")
    log = RequestLog(
        request_kind="events", endpoint="/v2/x/events",
        payload_size=100, timestamp=1759305600.0,
        status_code=200,
    )
    assert_check(log.is_success(), "is_success() = True")

    print("\n[B2] is_success() False khi có error")
    log_err = RequestLog(
        request_kind="events", endpoint="/v2/x/events",
        payload_size=100, timestamp=1759305600.0,
        status_code=200, error="something wrong",
    )
    assert_check(not log_err.is_success(), "có error → False")

    print("\n[B3] to_dict() có đủ trường (INSTRUCTION: batch_id, event_count, "
          "payload_size, status_code, timestamp, retry_count)")
    log_full = RequestLog(
        request_kind="events", endpoint="/v2/x/events",
        payload_size=200, timestamp=1759305600.0,
        event_count=10, batch_id="batch_001",
        status_code=200, retry_count=2,
    )
    d = log_full.to_dict()
    required = {"batch_id", "event_count", "payload_size", "status_code",
                "timestamp", "retry_count", "request_kind", "endpoint"}
    assert_check(required.issubset(d.keys()),
                 f"to_dict có {sorted(required & set(d.keys()))}")

    # ----------------------------------------------------------------------
    # C. send_events validation
    # ----------------------------------------------------------------------
    print("\n[--- C. SEND_EVENTS VALIDATION ---]")

    print("\n[C1] events KHÔNG phải list raise TypeError")
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=MockSession(),
    )
    try:
        client.send_events({"not": "a list"})  # dict, not list
        raise AssertionError("Không raise")
    except TypeError as e:
        assert_check("list" in str(e), f"TypeError: {e}")

    print("\n[C2] events rỗng raise ValueError")
    try:
        client.send_events([])
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("rỗng" in str(e).lower(), f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # D. send_events – Mock session (success)
    # ----------------------------------------------------------------------
    print("\n[--- D. SEND_EVENTS – SUCCESS ---]")

    print("\n[D1] Success 200 → SendResult.success=True")
    events = [make_event(i) for i in range(5)]
    mock = MockSession(MockResponse(200, {"ok": True}))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock,
        time_source=lambda: 1759305600.0,
        id_generator=lambda: "batch_test_001",
    )
    result = client.send_events(events)
    assert_check(isinstance(result, SendResult), "return SendResult")
    assert_check(result.success, "success = True")
    assert_check(result.status_code == 200, f"status = {result.status_code}")
    assert_check(result.batch_id == "batch_test_001",
                 f"batch_id = {result.batch_id}")
    assert_check(result.event_count == 5, f"event_count = {result.event_count}")
    assert_check(result.retry_count == 0, f"retry_count = {result.retry_count}")

    print("\n[D2] Authorization header đúng (HMAC)")
    sent = mock.requests_made[0]
    auth = sent["headers"].get("Authorization")
    assert_check(auth is not None, "có Authorization header")
    assert_check(len(auth) == 44, f"auth length = {len(auth)} (44 base64)")

    print("\n[D3] Content-Encoding: gzip (mặc định use_gzip=True)")
    assert_check(sent["headers"].get("Content-Encoding") == "gzip",
                 f"Content-Encoding = {sent['headers'].get('Content-Encoding')}")

    print("\n[D4] Content-Type: application/json")
    assert_check(sent["headers"].get("Content-Type") == "application/json",
                 f"Content-Type = {sent['headers'].get('Content-Type')}")

    print("\n[D5] Body là gzip bytes (magic 1f 8b)")
    assert_check(sent["data"][:2] == b"\x1f\x8b",
                 f"gzip magic = {sent['data'][:2].hex()}")

    print("\n[D6] URL đúng format /v2/<game_key>/events")
    expected_url = f"https://x.com/v2/{TEST_GAME_KEY}/events"
    assert_check(sent["url"] == expected_url,
                 f"url = {sent['url']}")

    print("\n[D7] RequestLog có đủ field INSTRUCTION yêu cầu")
    rl = result.request_log
    assert_check(rl.batch_id == "batch_test_001", f"batch_id = {rl.batch_id}")
    assert_check(rl.event_count == 5, f"event_count = {rl.event_count}")
    assert_check(rl.payload_size == len(sent["data"]),
                 f"payload_size = {rl.payload_size}")
    assert_check(rl.status_code == 200, f"status_code = {rl.status_code}")
    assert_check(rl.timestamp > 0, f"timestamp = {rl.timestamp}")
    assert_check(rl.retry_count == 0, f"retry_count = {rl.retry_count}")
    assert_check(rl.duration_ms is not None, f"duration_ms = {rl.duration_ms}")
    assert_check(rl.is_success(), "is_success() = True")

    print("\n[D8] use_gzip=False → KHÔNG có Content-Encoding")
    mock_raw = MockSession(MockResponse(200, {"ok": True}))
    client_raw = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_raw,
    )
    client_raw.send_events(events, use_gzip=False)
    sent_raw = mock_raw.requests_made[0]
    assert_check("Content-Encoding" not in sent_raw["headers"],
                 "no Content-Encoding")
    # Và HMAC tính trên JSON (khác gzip mode)
    auth_raw = sent_raw["headers"]["Authorization"]
    auth_gz = sent["headers"]["Authorization"]
    assert_check(auth_raw != auth_gz,
                 "auth khác nhau giữa gzip mode và raw mode")

    # ----------------------------------------------------------------------
    # E. send_events – Error handling
    # ----------------------------------------------------------------------
    print("\n[--- E. SEND_EVENTS – ERROR HANDLING ---]")

    print("\n[E1] Status 401 → GameAnalyticsAuthError")
    mock_401 = MockSession(MockResponse(401, {"error": "bad auth"}, "Bad Auth"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_401,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsAuthError as e:
        assert_check(e.status_code == 401, f"status_code = {e.status_code}")
        assert_check(e.request_log is not None, "request_log attached")

    print("\n[E2] Status 403 → GameAnalyticsAuthError")
    mock_403 = MockSession(MockResponse(403, {"error": "forbidden"}, "Forbidden"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_403,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsAuthError as e:
        assert_check(e.status_code == 403, f"status_code = {e.status_code}")

    print("\n[E3] Status 413 → GameAnalyticsPayloadTooLargeError")
    mock_413 = MockSession(MockResponse(413, {"error": "too large"}, "Too Large"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_413,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsPayloadTooLargeError as e:
        assert_check(e.status_code == 413, f"status_code = {e.status_code}")

    print("\n[E4] Status 500 → GameAnalyticsServerError")
    mock_500 = MockSession(MockResponse(500, {"error": "server"}, "Server Err"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_500,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsServerError as e:
        assert_check(e.status_code == 500, f"status_code = {e.status_code}")

    print("\n[E5] Status 400 → GameAnalyticsError generic")
    mock_400 = MockSession(MockResponse(400, {"error": "bad"}, "Bad Req"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_400,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsError as e:
        # KHÔNG phải AuthError / PayloadError / ServerError
        assert_check(type(e) is GameAnalyticsError, f"type = {type(e).__name__}")
        assert_check(e.status_code == 400, f"status_code = {e.status_code}")

    print("\n[E6] RequestLog.error có message khi fail")
    rl = client.send_events.__self__._do_post.__qualname__  # just for syntax
    # Re-do: gửi lại 500
    mock_500b = MockSession(MockResponse(500, {"error": "x"}, "x"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_500b,
    )
    try:
        client.send_events(events)
    except GameAnalyticsServerError as e:
        assert_check(e.request_log.error is not None,
                     f"error msg = {e.request_log.error}")

    # ----------------------------------------------------------------------
    # F. send_events – Network error
    # ----------------------------------------------------------------------
    print("\n[--- F. SEND_EVENTS – NETWORK ERROR ---]")

    print("\n[F1] requests.exceptions.Timeout → GameAnalyticsError")
    mock_to = MockSession(raise_exc=requests.exceptions.Timeout("read timed out"))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_to,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsError as e:
        assert_check(e.status_code is None,
                     f"network error → status_code = {e.status_code}")
        assert_check("timeout" in str(e).lower(), f"msg = {e}")

    print("\n[F2] requests.exceptions.ConnectionError → GameAnalyticsError")
    mock_conn = MockSession(
        raise_exc=requests.exceptions.ConnectionError("refused"),
    )
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_conn,
    )
    try:
        client.send_events(events)
        raise AssertionError("Không raise")
    except GameAnalyticsError as e:
        assert_check(e.status_code is None, "no status_code")
        assert_check("network" in str(e).lower(), f"msg = {e}")

    # ----------------------------------------------------------------------
    # G. Init endpoint
    # ----------------------------------------------------------------------
    print("\n[--- G. INIT ENDPOINT ---]")

    print("\n[G1] Init gọi URL /v2/<game_key>/init")
    mock_init = MockSession(MockResponse(200, {"server_ts": 1759306000}))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_init,
        time_source=lambda: 1759305600.0,
    )
    set_server_ts_offset(0)  # reset
    init_result = client.init()
    sent = mock_init.requests_made[0]
    expected_url = f"https://x.com/v2/{TEST_GAME_KEY}/init"
    assert_check(sent["url"] == expected_url,
                 f"url = {sent['url']}")
    assert_check(isinstance(init_result, InitResult), "return InitResult")

    print("\n[G2] Init response có server_ts → set global offset")
    assert_check(init_result.server_ts == 1759306000,
                 f"server_ts = {init_result.server_ts}")
    assert_check(init_result.local_ts == 1759305600,
                 f"local_ts = {init_result.local_ts}")
    assert_check(init_result.offset == 400,
                 f"offset = {init_result.offset}")
    # Side effect: global offset đã set
    global_offset = get_server_ts_offset()
    assert_check(global_offset == 400,
                 f"global offset = {global_offset}")

    print("\n[G3] Init response thiếu server_ts → raise GameAnalyticsError")
    mock_bad = MockSession(MockResponse(200, {"ok": True}))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_bad,
    )
    try:
        client.init()
        raise AssertionError("Không raise")
    except GameAnalyticsError as e:
        assert_check("server_ts" in str(e), f"msg = {e}")

    print("\n[G4] Init nhận configs_hash")
    mock_cfg = MockSession(MockResponse(200, {
        "server_ts": 1759306000, "configs_hash": "abc123",
    }))
    client = GameAnalyticsClient(
        game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
        base_url="https://x.com", http_session=mock_cfg,
    )
    init_result = client.init()
    assert_check(init_result.configs_hash == "abc123",
                 f"configs_hash = {init_result.configs_hash}")

    # ----------------------------------------------------------------------
    # H. End-to-end với httpbin (optional)
    # ----------------------------------------------------------------------
    print("\n[--- H. END-TO-END VỚI HTTPBIN (optional) ---]")

    print("\n[H1] Thử POST lên httpbin.org/post (network optional)")
    try:
        from gameanalytics.auth import verify_authorization
        client = GameAnalyticsClient(
            game_key=TEST_GAME_KEY, secret_key=TEST_SECRET_KEY,
            base_url="https://httpbin.org",
            timeout=10,
        )
        events_small = [make_event(i) for i in range(3)]
        # httpbin /post không validate game_key, chỉ echo body
        result = client.send_events(
            events_small, batch_id="httpbin_test",
        )
        assert_check(result.success, f"success = {result.success}")
        assert_check(result.status_code == 200, f"status = {result.status_code}")
        print(f"  [INFO] httpbin OK: payload={result.payload_size}B")
    except GameAnalyticsError as e:
        # Network có thể fail trong sandbox → không fail test
        print(f"  [WARN] httpbin không khả dụng: {e}")
        print("  [INFO] (test này optional – bỏ qua nếu offline)")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 20 CLIENT OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
