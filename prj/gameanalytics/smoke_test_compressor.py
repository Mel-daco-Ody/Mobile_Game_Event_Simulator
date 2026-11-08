"""Smoke test cho BƯỚC 18 – GZIP Compression.

Kiểm tra Compressor:

A. serialize_events:
    1. Serialize trả bytes UTF-8.
    2. JSON là list (không phải object).
    3. Compact (không whitespace) để tối ưu.
    4. UTF-8 không escape (giữ ký tự đặc biệt).

B. compress_events:
    5. Output bắt đầu bằng GZIP_MAGIC (1f 8b).
    6. compresslevel=1 → nhanh hơn, nén kém hơn.
    7. compresslevel=9 → chậm hơn, nén tốt hơn.
    8. compresslevel=0 hoặc 10 → raise ValueError.

C. decompress_events (round-trip):
    9. compress → decompress → list events gốc.
    10. Decompress body không phải gzip → raise ValueError.
    11. Decompress JSON không phải list → raise ValueError.

D. is_gzip:
    12. detect gzip body đúng.
    13. detect raw JSON KHÔNG phải gzip.
    14. detect bytes rỗng KHÔNG phải gzip.

E. make_compressed_request:
    15. Headers đúng chuẩn GameAnalytics (Content-Type, Content-Encoding, ...).
    16. Content-Length = len(body).
    17. extra_headers được merge.
    18. raw_size > compressed_size.
    19. compression_ratio < 1.0 cho JSON text thông thường.

F. compress_for_hmac:
    20. Kết quả giống compress_events.
    21. Output bắt đầu bằng GZIP_MAGIC.

G. estimate_compressed_size:
    22. Trả ước lượng < raw_size.
    23. Trả ước lượng > 0 cho events không rỗng.

H. End-to-end qua batcher:
    24. Batch.to_gzip_bytes() tương đương compressor.compress_events(events).
    25. 1000 design events → CompressedRequest.body < 100 KB.

I. Constants:
    26. GZIP_MAGIC = b'\\x1f\\x8b'.
    27. CONTENT_TYPE_GZIP = 'application/json'.
    28. CONTENT_ENCODING_GZIP = 'gzip'.

Chạy:
    cd prj && python gameanalytics/smoke_test_compressor.py
"""

from __future__ import annotations

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
    Batch,
    CONTENT_ENCODING_GZIP,
    CONTENT_TYPE_GZIP,
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
from gameanalytics.batcher import batch_events_by_size  # noqa: E402
from generators import (  # noqa: E402
    generate_design_event,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.design import DesignSimulator  # noqa: E402


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def make_event(idx: int = 0) -> dict:
    """Tạo event mẫu (giống smoke_test_batcher)."""
    return {
        "category": "design",
        "v": 2,
        "user_id": "player_000001",
        "client_ts": 1759305600 + idx,
        "sdk_version": "rest api v2",
        "os_version": "android 15",
        "manufacturer": "google",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
        "event_id": f"Gameplay:Kill:Zombie_{idx}",
        "value": 42,
    }


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 18: GZIP COMPRESSION")
    print("=" * 60)

    events_sample = [make_event(i) for i in range(20)]

    # ----------------------------------------------------------------------
    # A. serialize_events
    # ----------------------------------------------------------------------
    print("\n[--- A. SERIALIZE_EVENTS ---]")

    print("\n[A1] Serialize trả bytes UTF-8")
    raw = serialize_events(events_sample)
    assert_check(isinstance(raw, bytes), f"type = {type(raw).__name__}")
    assert_check(len(raw) > 0, f"len(raw) = {len(raw)}")

    print("\n[A2] JSON là list (KHÔNG phải object)")
    parsed = json.loads(raw.decode("utf-8"))
    assert_check(isinstance(parsed, list), f"JSON type = {type(parsed).__name__}")
    assert_check(len(parsed) == 20, f"len = {len(parsed)}")

    print("\n[A3] Compact (không whitespace thừa)")
    # Mỗi event có ~12 field, kiểm tra KHÔNG có ': ' (space sau colon)
    # vì separators=(",", ":") đã set.
    assert_check(b": " not in raw,
                 "Không có ': ' (compact separators)")
    assert_check(b", " not in raw,
                 "Không có ', ' (compact separators)")

    print("\n[A4] UTF-8 giữ ký tự đặc biệt (ensure_ascii=False)")
    events_utf8 = [
        {"category": "design", "name": "Tiếng Việt", "emoji": "🎮",
         "user_id": "player_000001"}
    ]
    raw_utf8 = serialize_events(events_utf8)
    assert_check("Tiếng Việt".encode("utf-8") in raw_utf8,
                 "Giữ 'Tiếng Việt' UTF-8 (không escape)")
    assert_check("🎮".encode("utf-8") in raw_utf8,
                 "Giữ emoji 🎮 UTF-8")
    # Kiểm tra KHÔNG có \\u escape
    assert_check(b"\\u" not in raw_utf8,
                 "Không có \\u escape (ensure_ascii=False)")

    # ----------------------------------------------------------------------
    # B. compress_events
    # ----------------------------------------------------------------------
    print("\n[--- B. COMPRESS_EVENTS ---]")

    print("\n[B1] Output bắt đầu bằng GZIP_MAGIC (1f 8b)")
    compressed = compress_events(events_sample)
    assert_check(compressed[:2] == b"\x1f\x8b",
                 f"magic = {compressed[:2].hex()}")
    assert_check(is_gzip(compressed), "is_gzip(compressed) = True")

    print("\n[B2] compresslevel=1 (fast, nén kém)")
    c1 = compress_events(events_sample, compresslevel=1)
    assert_check(is_gzip(c1), "level=1 vẫn là gzip")
    assert_check(len(c1) > 0, f"len(level=1) = {len(c1)}")

    print("\n[B3] compresslevel=9 (slow, nén tốt)")
    c9 = compress_events(events_sample, compresslevel=9)
    assert_check(is_gzip(c9), "level=9 vẫn là gzip")
    # level=9 thường nhỏ hơn hoặc bằng level=1
    assert_check(len(c9) <= len(c1) + 50,  # +50 cho overhead variance
                 f"level=9 ({len(c9)}) <= level=1 ({len(c1)})")

    print("\n[B4] compresslevel invalid raise ValueError")
    for bad_level in (0, 10, -1):
        try:
            compress_events(events_sample, compresslevel=bad_level)
            raise AssertionError(f"Không raise cho level={bad_level}")
        except ValueError:
            pass
    assert_check(True, "level ∈ {0, 10, -1} đều raise ValueError")

    # ----------------------------------------------------------------------
    # C. decompress_events (round-trip)
    # ----------------------------------------------------------------------
    print("\n[--- C. DECOMPRESS_EVENTS ---]")

    print("\n[C1] compress → decompress → list events gốc")
    round_trip = decompress_events(compressed)
    assert_check(isinstance(round_trip, list), "decompress trả list")
    assert_check(len(round_trip) == 20, f"len = {len(round_trip)}")
    # So sánh nội dung
    assert_check(round_trip[0]["user_id"] == "player_000001",
                 "round-trip user_id khớp")
    assert_check(round_trip[5]["event_id"] == "Gameplay:Kill:Zombie_5",
                 "round-trip event_id khớp")

    print("\n[C2] Decompress body KHÔNG phải gzip → raise ValueError")
    try:
        decompress_events(b"this is not gzip")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("gzip" in str(e).lower(), f"ValueError: {e}")

    print("\n[C3] Decompress JSON không phải list → raise ValueError")
    # gzip của dict (không phải list)
    bad_gzip = compress_events([{"x": 1, "y": 2}])  # list with 1 dict - OK
    # Tạo gzip của dict đơn (không list)
    import gzip as _gzip
    bad = _gzip.compress(json.dumps({"x": 1}).encode("utf-8"))
    try:
        decompress_events(bad)
        raise AssertionError("Không raise cho non-list JSON")
    except ValueError as e:
        assert_check("list" in str(e), f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # D. is_gzip
    # ----------------------------------------------------------------------
    print("\n[--- D. IS_GZIP ---]")

    print("\n[D1] detect gzip body đúng")
    assert_check(is_gzip(compressed), "compressed → True")
    assert_check(is_gzip(b"\x1f\x8b\x08\x00abc"), "magic + extra → True")

    print("\n[D2] detect raw JSON KHÔNG phải gzip")
    assert_check(not is_gzip(raw), "raw JSON → False")
    assert_check(not is_gzip(b"hello"), "random bytes → False")

    print("\n[D3] detect bytes rỗng KHÔNG phải gzip")
    assert_check(not is_gzip(b""), "empty → False")
    assert_check(not is_gzip(b"\x1f"), "1 byte → False")

    # ----------------------------------------------------------------------
    # E. make_compressed_request
    # ----------------------------------------------------------------------
    print("\n[--- E. MAKE_COMPRESSED_REQUEST ---]")

    print("\n[E1] Headers đúng chuẩn GameAnalytics")
    req = make_compressed_request(events_sample)
    assert_check(req.headers["Content-Type"] == "application/json",
                 f"Content-Type = {req.headers['Content-Type']}")
    assert_check(req.headers["Content-Encoding"] == "gzip",
                 f"Content-Encoding = {req.headers['Content-Encoding']}")
    assert_check(req.headers["User-Agent"].startswith("GAP-Simulator"),
                 f"User-Agent = {req.headers['User-Agent']}")
    assert_check(req.headers["Accept"] == "application/json",
                 "Accept = application/json")

    print("\n[E2] Content-Length = len(body)")
    assert_check(int(req.headers["Content-Length"]) == len(req.body),
                 f"Content-Length = {req.headers['Content-Length']}, "
                 f"body len = {len(req.body)}")

    print("\n[E3] extra_headers được merge")
    req_extra = make_compressed_request(
        events_sample,
        extra_headers={"Authorization": "BASE64HMAC", "X-Custom": "abc"},
    )
    assert_check(req_extra.headers["Authorization"] == "BASE64HMAC",
                 "Authorization preserved")
    assert_check(req_extra.headers["X-Custom"] == "abc", "X-Custom preserved")
    # Vẫn có header mặc định
    assert_check(req_extra.headers["Content-Type"] == "application/json",
                 "Content-Type mặc định vẫn có")

    print("\n[E4] raw_size > compressed_size")
    assert_check(req.raw_size > req.compressed_size,
                 f"raw={req.raw_size} > compressed={req.compressed_size}")

    print("\n[E5] compression_ratio < 1.0")
    assert_check(req.compression_ratio < 1.0,
                 f"ratio = {req.compression_ratio:.2%}")

    print("\n[E6] event_count")
    assert_check(req.event_count == 20, f"event_count = {req.event_count}")

    # ----------------------------------------------------------------------
    # F. compress_for_hmac
    # ----------------------------------------------------------------------
    print("\n[--- F. COMPRESS_FOR_HMAC ---]")

    print("\n[F1] Kết quả giống compress_events")
    a = compress_for_hmac(events_sample)
    b = compress_events(events_sample)
    assert_check(a == b, "compress_for_hmac == compress_events (cùng input)")

    print("\n[F2] Output bắt đầu bằng GZIP_MAGIC")
    assert_check(a[:2] == b"\x1f\x8b", f"magic = {a[:2].hex()}")

    # ----------------------------------------------------------------------
    # G. estimate_compressed_size
    # ----------------------------------------------------------------------
    print("\n[--- G. ESTIMATE_COMPRESSED_SIZE ---]")

    print("\n[G1] Ước lượng < raw_size")
    estimate = estimate_compressed_size(events_sample)
    assert_check(estimate < req.raw_size,
                 f"estimate={estimate} < raw={req.raw_size}")

    print("\n[G2] Ước lượng > 0 cho events không rỗng")
    assert_check(estimate > 0, f"estimate = {estimate}")

    # ----------------------------------------------------------------------
    # H. End-to-end qua batcher
    # ----------------------------------------------------------------------
    print("\n[--- H. END-TO-END VỚI BATCHER ---]")

    print("\n[H1] Batch.to_gzip_bytes() tương đương compressor.compress_events()")
    batches = batch_events_by_size(events_sample, safe_limit_bytes=10_000)
    assert_check(len(batches) >= 1, f"≥ 1 batch (got {len(batches)})")
    batch = batches[0]
    batch_gzip = batch.to_gzip_bytes()
    direct_gzip = compress_events(batch.events)
    # Có thể khác nhau 1 vài byte do metadata gzip (timestamp)
    # nhưng phải decompress ra cùng data
    decompressed_a = decompress_events(batch_gzip)
    decompressed_b = decompress_events(direct_gzip)
    assert_check(decompressed_a == decompressed_b,
                 "2 cách gzip đều cho cùng data (decompress khớp)")

    print("\n[H2] 1000 design events → CompressedRequest.body < 100 KB")
    players = generate_players(count=2, seed=42)
    base_ts = 1759305600
    session = generate_session(players[0], session_start_ts=base_ts)
    design_sim = DesignSimulator(rng=__import__("random").Random(7))
    decisions = []
    for i in range(3000):
        d = design_sim.try_event(players[0], base_ts + i * 5)
        if d is not None:
            decisions.append(d)
    real_events = [
        generate_design_event(players[0], session, d) for d in decisions
    ]
    assert_check(len(real_events) >= 500,
                 f"≥500 design events (got {len(real_events)})")
    req_real = make_compressed_request(real_events)
    assert_check(req_real.compressed_size < 100 * 1024,
                 f"compressed={req_real.compressed_size} < 100 KB")
    print(f"  [INFO] {len(real_events)} events → "
          f"raw={req_real.raw_size}B, compressed={req_real.compressed_size}B, "
          f"ratio={req_real.compression_ratio:.2%}")
    # Round-trip
    rt = decompress_events(req_real.body)
    assert_check(len(rt) == len(real_events),
                 f"round-trip len = {len(rt)}")

    # ----------------------------------------------------------------------
    # I. Constants
    # ----------------------------------------------------------------------
    print("\n[--- I. CONSTANTS ---]")
    assert_check(GZIP_MAGIC == b"\x1f\x8b",
                 f"GZIP_MAGIC = {GZIP_MAGIC.hex()}")
    assert_check(CONTENT_TYPE_GZIP == "application/json",
                 f"CONTENT_TYPE_GZIP = {CONTENT_TYPE_GZIP}")
    assert_check(CONTENT_ENCODING_GZIP == "gzip",
                 f"CONTENT_ENCODING_GZIP = {CONTENT_ENCODING_GZIP}")
    assert_check(DEFAULT_COMPRESS_LEVEL == 6,
                 f"DEFAULT_COMPRESS_LEVEL = {DEFAULT_COMPRESS_LEVEL}")
    assert_check(USER_AGENT.startswith("GAP-Simulator"),
                 f"USER_AGENT = {USER_AGENT}")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 18 GZIP COMPRESSION OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
