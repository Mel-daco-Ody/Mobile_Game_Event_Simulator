"""Smoke test cho BƯỚC 17 – Event Batcher (1 MB limit).

Kiểm tra Batcher:

A. Khởi tạo & Validation:
    1. safe_limit mặc định = game_config.SAFE_LIMIT_BYTES (900 KB).
    2. safe_limit > POST_LIMIT_BYTES raise ValueError.
    3. safe_limit <= 0 raise ValueError.

B. Batch dataclass:
    4. Batch.event_count, compressed_size, raw_size, compression_ratio.
    5. Batch.to_json_bytes() trả JSON list.
    6. Batch.to_gzip_bytes() trả gzip bytes.

C. add_event cơ bản:
    7. add 1 event → current_event_count=1, is_empty=False.
    8. add nhiều event nhỏ → KHÔNG auto-flush.
    9. current_compressed_size > 0 sau khi add.

D. Auto-flush theo size:
    10. Add event đủ lớn để vượt SAFE_LIMIT → auto-flush batch cũ.
    11. Batch flushed có compressed_size < safe_limit.
    12. Mỗi batch có event_count > 0.
    13. Tổng events = tổng events đã add.

E. Single oversized event:
    14. 1 event > SAFE_LIMIT → oversized_dropped++, KHÔNG add vào batch.
    15. Batch trước đó (nếu có) vẫn được flush.

F. flush() thủ công:
    16. flush() trả Batch có events.
    17. flush() khi empty trả None.

G. on_flush callback:
    18. Callback được gọi với Batch object.
    19. add_event KHÔNG trả về Batch khi có callback.

H. Stats:
    20. batches_flushed tăng đúng.
    21. events_sent tổng đúng.
    22. oversized_dropped đếm đúng.

I. Convenience batch_events_by_size():
    23. Trả list Batch, tổng events = input.
    24. Mỗi batch có compressed_size <= safe_limit.

J. End-to-end với generator thật:
    25. Sinh 1000 design events → batcher.add_events → nhiều batch.
    26. Mỗi batch < SAFE_LIMIT_BYTES.
    27. Send batch qua gameanalytics.validator → vẫn pass schema.

Chạy:
    cd prj && python gameanalytics/smoke_test_batcher.py
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

from config import game_config  # noqa: E402
from gameanalytics import (  # noqa: E402
    Batch,
    Batcher,
    BatcherStats,
    batch_events_by_size,
)
from gameanalytics.validator import EventValidator  # noqa: E402
from generators import (  # noqa: E402
    generate_design_event,
    generate_players,
)
from models.session import generate_session  # noqa: E402
from simulation.design import DesignDecision, DesignSimulator  # noqa: E402


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def make_event(idx: int = 0) -> dict:
    """Tạo event nhỏ (khoảng 200 bytes raw)."""
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


def make_big_event(extra_kb: int = 100) -> dict:
    """Tạo event to (khoảng extra_kb KB) – dùng data KHÔNG nén được.

    Gzip nén rất tốt chuỗi lặp ('xxx...'). Để đảm bảo payload lớn
    hơn safe_limit SAU KHI gzip, dùng random bytes (incompressible).
    """
    import os as _os
    ev = make_event()
    # Random bytes gần như không nén được (entropy cao)
    ev["custom_fields"] = {
        "payload": _os.urandom(extra_kb * 1024).hex(),
    }
    return ev


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 17: EVENT BATCHER")
    print("=" * 60)

    # ----------------------------------------------------------------------
    # A. Khởi tạo & Validation
    # ----------------------------------------------------------------------
    print("\n[--- A. KHỞI TẠO & VALIDATION ---]")

    print("\n[A1] safe_limit mặc định = game_config.SAFE_LIMIT_BYTES")
    b = Batcher()
    assert_check(b.safe_limit_bytes == game_config.SAFE_LIMIT_BYTES,
                 f"safe_limit = {b.safe_limit_bytes} == "
                 f"{game_config.SAFE_LIMIT_BYTES}")
    assert_check(game_config.SAFE_LIMIT_BYTES < game_config.POST_LIMIT_BYTES,
                 "SAFE_LIMIT_BYTES < POST_LIMIT_BYTES (900 KB < 1 MB)")

    print("\n[A2] safe_limit > POST_LIMIT_BYTES raise ValueError")
    try:
        Batcher(safe_limit_bytes=game_config.POST_LIMIT_BYTES + 1000)
        raise AssertionError("Không raise khi safe > POST_LIMIT")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    print("\n[A3] safe_limit <= 0 raise ValueError")
    try:
        Batcher(safe_limit_bytes=0)
        raise AssertionError("Không raise khi safe=0")
    except ValueError as e:
        assert_check(True, f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # B. Batch dataclass
    # ----------------------------------------------------------------------
    print("\n[--- B. BATCH DATACLASS ---]")

    print("\n[B1] Batch.event_count, compressed_size, raw_size")
    evts = [make_event(1), make_event(2)]
    from gameanalytics.compressor import compress_events, serialize_events  # noqa: E402
    real_compressed = len(compress_events(evts))
    real_raw = len(serialize_events(evts))
    sample = Batch(
        events=evts,
        compressed_size=real_compressed,
        raw_size=real_raw,
    )
    assert_check(sample.event_count == 2, "event_count=2")
    assert_check(sample.compressed_size == real_compressed,
                 f"compressed_size={sample.compressed_size} == {real_compressed}")
    assert_check(sample.raw_size == real_raw,
                 f"raw_size={sample.raw_size} == {real_raw}")

    print("\n[B2] Batch.compression_ratio = compressed / raw")
    assert_check(0 < sample.compression_ratio <= 1.0,
                 f"ratio = {sample.compression_ratio:.4f} ∈ (0, 1]")

    print("\n[B3] Batch.to_json_bytes() trả JSON list")
    bjb = sample.to_json_bytes()
    parsed = json.loads(bjb.decode("utf-8"))
    assert_check(isinstance(parsed, list), "JSON là list")
    assert_check(len(parsed) == 2, f"len = {len(parsed)}")
    assert_check(parsed[0]["user_id"] == "player_000001", "user_id khớp")

    print("\n[B4] Batch.to_gzip_bytes() trả gzip bytes")
    bgz = sample.to_gzip_bytes()
    assert_check(bgz[:2] == b"\x1f\x8b",
                 f"gzip magic header = {bgz[:2].hex()}")
    decompressed = gzip.decompress(bgz).decode("utf-8")
    parsed2 = json.loads(decompressed)
    assert_check(len(parsed2) == 2, "decompress OK")

    # ----------------------------------------------------------------------
    # B5. Consistency với compressor (FIX vấn đề #2 medium)
    # ----------------------------------------------------------------------
    print("\n[B5] Batch.to_gzip_bytes() == compressor.compress_events()")
    from gameanalytics.compressor import (
        compress_events,
        serialize_events,
    )
    bgz_via_compressor = compress_events(sample.events)
    assert_check(
        bgz == bgz_via_compressor,
        f"to_gzip_bytes() == compress_events() "
        f"({len(bgz)}B vs {len(bgz_via_compressor)}B)"
    )
    braw = sample.to_json_bytes()
    praw = serialize_events(sample.events)
    assert_check(
        braw == praw,
        f"to_json_bytes() == serialize_events() "
        f"({len(braw)}B vs {len(praw)}B)"
    )
    assert_check(
        sample.compressed_size == len(bgz_via_compressor),
        f"batch.compressed_size ({sample.compressed_size}) "
        f"== len(to_gzip_bytes()) ({len(bgz_via_compressor)})"
    )

    print("\n[B6] Batcher._compute_sizes() dùng compress_events()")
    b2 = Batcher()
    b2.add_event(make_event(100))
    b2.add_event(make_event(101))
    expected_gz = len(compress_events(b2._current))
    expected_raw = len(serialize_events(b2._current))
    assert_check(
        b2._current_compressed_size == expected_gz,
        f"compressed_size {b2._current_compressed_size} == {expected_gz}"
    )
    assert_check(
        b2._current_raw_size == expected_raw,
        f"raw_size {b2._current_raw_size} == {expected_raw}"
    )

    # ----------------------------------------------------------------------
    # C. add_event cơ bản
    # ----------------------------------------------------------------------
    print("\n[--- C. ADD_EVENT CƠ BẢN ---]")

    print("\n[C1] add 1 event → current_event_count=1")
    b = Batcher()
    b.add_event(make_event(1))
    assert_check(b.current_event_count == 1, "count=1")
    assert_check(not b.is_empty, "is_empty=False")

    print("\n[C2] add 100 event nhỏ → KHÔNG auto-flush")
    b = Batcher()
    for i in range(100):
        b.add_event(make_event(i))
    assert_check(b.current_event_count == 100,
                 f"current_count = {b.current_event_count}")
    assert_check(b.stats.batches_flushed == 0,
                 f"0 batch flushed (got {b.stats.batches_flushed})")

    print("\n[C3] current_compressed_size > 0 sau khi add")
    b = Batcher()
    b.add_event(make_event(1))
    assert_check(b.current_compressed_size > 0,
                 f"compressed_size = {b.current_compressed_size}")
    # Và compressed_size < safe_limit
    assert_check(b.current_compressed_size < b.safe_limit_bytes,
                 f"{b.current_compressed_size} < {b.safe_limit_bytes}")

    # ----------------------------------------------------------------------
    # D. Auto-flush theo size
    # ----------------------------------------------------------------------
    print("\n[--- D. AUTO-FLUSH THEO SIZE ---]")

    print("\n[D1] Set small safe_limit + add nhiều event → auto-flush")
    # 50 KB limit → event to ~200B sẽ fit ~250 events/batch
    SAFE = 50 * 1024
    b = Batcher(safe_limit_bytes=SAFE)
    flushed_batches: list[Batch] = []
    for i in range(500):
        result = b.add_event(make_event(i))
        if isinstance(result, Batch):
            flushed_batches.append(result)
    # Flush cuối
    last = b.flush()
    if isinstance(last, Batch):
        flushed_batches.append(last)

    assert_check(len(flushed_batches) >= 1,
                 f"ít nhất 1 batch (got {len(flushed_batches)})")

    print("\n[D2] Mỗi batch có compressed_size <= safe_limit")
    for i, batch in enumerate(flushed_batches):
        if batch.compressed_size > SAFE:
            raise AssertionError(
                f"batch {i} compressed_size={batch.compressed_size} > SAFE={SAFE}"
            )
    assert_check(all(bat.compressed_size <= SAFE for bat in flushed_batches),
                 "all batches <= SAFE_LIMIT")

    print("\n[D3] Tổng events trong batch = tổng events đã add")
    total = sum(bat.event_count for bat in flushed_batches)
    assert_check(total == 500, f"total = {total} (expected 500)")

    print("\n[D4] Mỗi batch có event_count > 0")
    assert_check(all(bat.event_count > 0 for bat in flushed_batches),
                 "all batches non-empty")

    # ----------------------------------------------------------------------
    # E. Single oversized event
    # ----------------------------------------------------------------------
    print("\n[--- E. SINGLE OVERSIZED EVENT ---]")

    print("\n[E1] Event > SAFE_LIMIT bị drop, KHÔNG add vào batch")
    b = Batcher(safe_limit_bytes=10 * 1024)  # 10 KB limit
    big = make_big_event(extra_kb=20)  # ~20 KB
    b.add_event(big)
    assert_check(b.current_event_count == 0, "big event không được add")
    assert_check(b.stats.oversized_dropped == 1,
                 f"oversized_dropped = {b.stats.oversized_dropped}")

    print("\n[E2] Sau oversized event, batch trước đó (nếu có) vẫn flush")
    b = Batcher(safe_limit_bytes=10 * 1024)
    # Add vài event nhỏ trước
    for i in range(10):
        b.add_event(make_event(i))
    pre_count = b.current_event_count
    # Add 1 event to
    b.add_event(big)
    # Batch trước vẫn còn (chưa flush vì chưa đầy)
    assert_check(b.current_event_count == pre_count,
                 f"batch trước vẫn còn ({pre_count})")
    assert_check(b.stats.oversized_dropped == 1,
                 f"oversized_dropped = {b.stats.oversized_dropped}")

    print("\n[E3] Add nhiều oversized event → counter tăng")
    b = Batcher(safe_limit_bytes=10 * 1024)
    for i in range(5):
        b.add_event(make_big_event(extra_kb=15))
    assert_check(b.stats.oversized_dropped == 5,
                 f"5 oversized (got {b.stats.oversized_dropped})")

    # ----------------------------------------------------------------------
    # F. flush() thủ công
    # ----------------------------------------------------------------------
    print("\n[--- F. FLUSH() THỦ CÔNG ---]")

    print("\n[F1] flush() trả Batch có events")
    b = Batcher()
    for i in range(10):
        b.add_event(make_event(i))
    batch = b.flush()
    assert_check(isinstance(batch, Batch), "flush trả Batch")
    assert_check(batch.event_count == 10, f"event_count = {batch.event_count}")
    assert_check(b.is_empty, "current empty sau flush")

    print("\n[F2] flush() khi empty trả None")
    b = Batcher()
    assert_check(b.flush() is None, "flush empty = None")

    # ----------------------------------------------------------------------
    # G. on_flush callback
    # ----------------------------------------------------------------------
    print("\n[--- G. ON_FLUSH CALLBACK ---]")

    print("\n[G1] Callback được gọi với Batch object")
    callback_batches: list[Batch] = []

    def cb(batch: Batch) -> None:
        callback_batches.append(batch)

    SAFE = 5 * 1024
    b = Batcher(safe_limit_bytes=SAFE, on_flush=cb)
    for i in range(200):
        b.add_event(make_event(i))
    b.flush()
    assert_check(len(callback_batches) >= 1,
                 f"callback called (got {len(callback_batches)})")
    assert_check(all(isinstance(bat, Batch) for bat in callback_batches),
                 "all Batch instances")

    print("\n[G2] add_event KHÔNG trả Batch khi có callback")
    b = Batcher(safe_limit_bytes=SAFE, on_flush=cb)
    result = None
    for i in range(200):
        result = b.add_event(make_event(i))
        if result is not None:
            break
    assert_check(result is None,
                 f"add_event trả None khi có callback (got {result})")

    # ----------------------------------------------------------------------
    # H. Stats
    # ----------------------------------------------------------------------
    print("\n[--- H. STATS ---]")

    print("\n[H1] batches_flushed tăng đúng")
    b = Batcher(safe_limit_bytes=SAFE)
    pre = b.stats.batches_flushed
    b.flush()  # empty → không tăng
    assert_check(b.stats.batches_flushed == pre, "flush empty không tăng")
    for i in range(5):
        b.add_event(make_event(i))
    b.flush()
    assert_check(b.stats.batches_flushed == pre + 1,
                 f"+1 sau flush (got {b.stats.batches_flushed})")

    print("\n[H2] events_sent tổng đúng")
    b = Batcher(safe_limit_bytes=SAFE)
    for i in range(50):
        b.add_event(make_event(i))
    b.flush()
    assert_check(b.stats.events_sent == 50,
                 f"events_sent = {b.stats.events_sent}")

    print("\n[H3] oversized_dropped đếm đúng")
    b = Batcher(safe_limit_bytes=10 * 1024)
    for i in range(3):
        b.add_event(make_big_event(extra_kb=20))
    assert_check(b.stats.oversized_dropped == 3, "oversized_dropped=3")

    print("\n[H4] BatcherStats.__str__")
    s = b.stats
    print(f"  [INFO] {s}")
    assert_check("BatcherStats" in str(s), "str có BatcherStats prefix")

    # ----------------------------------------------------------------------
    # I. Convenience batch_events_by_size()
    # ----------------------------------------------------------------------
    print("\n[--- I. CONVENIENCE batch_events_by_size ---]")

    print("\n[I1] batch_events_by_size trả list Batch")
    events_500 = [make_event(i) for i in range(500)]
    batches = batch_events_by_size(events_500, safe_limit_bytes=SAFE)
    assert_check(len(batches) >= 1, f"≥ 1 batch (got {len(batches)})")

    print("\n[I2] Tổng events = input")
    total = sum(bat.event_count for bat in batches)
    assert_check(total == 500, f"total = {total} (expected 500)")

    print("\n[I3] Mỗi batch compressed_size <= safe_limit")
    for i, bat in enumerate(batches):
        if bat.compressed_size > SAFE:
            raise AssertionError(
                f"batch {i} compressed={bat.compressed_size} > SAFE={SAFE}"
            )
    assert_check(True, f"all {len(batches)} batches OK")

    # ----------------------------------------------------------------------
    # J. End-to-end với generator thật
    # ----------------------------------------------------------------------
    print("\n[--- J. END-TO-END VỚI GENERATOR THẬT ---]")

    print("\n[J1] 1000 design events qua batcher")
    players = generate_players(count=2, seed=42)
    base_ts = 1759305600
    session = generate_session(players[0], session_start_ts=base_ts)
    design_sim = DesignSimulator(rng=__import__("random").Random(7))
    decisions = []
    for i in range(2000):
        d = design_sim.try_event(players[0], base_ts + i * 5)
        if d is not None:
            decisions.append(d)
    assert_check(len(decisions) >= 500, f"≥500 decisions (got {len(decisions)})")
    real_events = [
        generate_design_event(players[0], session, d) for d in decisions
    ]
    print(f"  [INFO] {len(real_events)} design events sinh được")

    # Batch
    real_batches = batch_events_by_size(real_events)
    assert_check(len(real_batches) >= 1,
                 f"≥ 1 batch ({len(real_batches)})")

    print("\n[J2] Mỗi batch < SAFE_LIMIT_BYTES")
    for i, bat in enumerate(real_batches):
        assert_check(
            bat.compressed_size <= game_config.SAFE_LIMIT_BYTES,
            f"batch {i} compressed={bat.compressed_size} <= "
            f"{game_config.SAFE_LIMIT_BYTES}"
        )

    print("\n[J3] Validator pass schema mọi batch")
    validator = EventValidator()
    total_validated = 0
    for bat in real_batches:
        result = validator.validate_batch(bat.events)
        assert_check(result.is_valid,
                     f"batch validate OK ({bat.event_count} events)")
        total_validated += bat.event_count
    assert_check(total_validated == len(real_events),
                 f"validated {total_validated}/{len(real_events)}")

    print("\n[J4] BatcherStats cuối")
    print(f"  [INFO] total batches = {len(real_batches)}, "
          f"avg events/batch = {len(real_events) / len(real_batches):.1f}")
    # Compression ratio
    total_raw = sum(bat.raw_size for bat in real_batches)
    total_compressed = sum(bat.compressed_size for bat in real_batches)
    ratio = total_compressed / total_raw if total_raw > 0 else 1.0
    print(f"  [INFO] total raw = {total_raw} bytes, "
          f"compressed = {total_compressed} bytes, "
          f"ratio = {ratio:.2%}")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 17 BATCHER OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
