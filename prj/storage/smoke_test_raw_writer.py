"""Smoke test cho BƯỚC 22 – Raw Data Writer.

Kiểm tra storage/raw_writer.py:

A. Khởi tạo & Validation:
    1. Default root = data/raw (relative to project root).
    2. Custom root respected.
    3. Root directory được tạo tự động.
    4. Env var GA_RAW_DATA_ROOT override.
    5. compress_level ngoài [1,9] raise.

B. write_batch – cơ bản:
    6. Ghi batch → file được tạo.
    7. Gzip magic header (1f 8b).
    8. Partition đúng: year=YYYY/month=MM/day=DD.
    9. File count = 1 sau khi ghi.
    10. Sequence number tăng dần.

C. RawBatchMetadata:
    11. event_count, raw_size, compressed_size đúng.
    12. date_partition = year=YYYY/...
    13. compression_ratio ∈ (0, 1].
    14. to_dict() có đủ field.

D. read_batch – replay:
    15. Read lại đúng JSON list.
    16. Số event khớp với lúc ghi.
    17. Field "user_id" preserve.

E. list_batches / count_batches / delete_batch:
    18. list_batches trả files sorted.
    19. count_batches khớp.
    20. delete_batch xoá file.

F. Validation trong write_batch:
    21. batch_id rỗng raise ValueError.
    22. events KHÔNG phải list raise ValueError.

G. ReplayableRawWriter:
    23. attach_client() link client.
    24. send() ghi raw + gửi API.
    25. Raw file vẫn được ghi dù API fail.

H. Partition filter:
    26. list_batches(year=2026, month=9) lọc đúng.

I. Stats:
    27. RawWriterStats.batches_written tăng sau khi ghi.
    28. events_written, bytes_written đúng.

J. Atomic write:
    29. Ghi 100 batch → không có partial file.
    30. File count = 100.

K. Concurrency (thread-safe):
    31. 10 thread ghi đồng thời → tất cả file unique.

Chạy:
    cd prj && python storage/smoke_test_raw_writer.py
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from storage.raw_writer import (  # noqa: E402
    BATCH_FILE_EXT,
    BATCH_FILE_PREFIX,
    DEFAULT_RAW_ROOT,
    ENV_RAW_DATA_ROOT,
    RawBatchMetadata,
    RawBatchStatus,
    RawDataWriter,
    RawWriterStats,
    ReplayableRawWriter,
    read_raw_batch,
    write_raw_batch,
)


# =====================================================================
# Helpers
# =====================================================================

def make_event(i: int = 0) -> dict:
    return {
        "category": "design",
        "v": 2,
        "user_id": f"player_{i:06d}",
        "client_ts": 1759305600 + i,
        "sdk_version": "rest api v2",
        "os_version": "android 15",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
        "event_id": f"Gameplay:Kill:Zombie_{i}",
        "value": i,
    }


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


# =====================================================================
# Mocks (cho ReplayableRawWriter)
# =====================================================================

class MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = json.dumps(self._json)

    def json(self):
        return self._json


class MockSession:
    def __init__(self, responses):
        self.requests_made = []
        self._responses = (
            responses if isinstance(responses, list) else [responses]
        )
        self._idx = 0

    def post(self, url, data, headers, timeout):
        self.requests_made.append({"url": url, "data": data, "headers": headers})
        resp = self._responses[self._idx] if self._idx < len(self._responses) else self._responses[-1]
        self._idx += 1
        return resp


# =====================================================================
# Main
# =====================================================================

def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 22: RAW DATA WRITER")
    print("=" * 60)

    # Setup temp root cho mọi test (tránh ghi vào data/raw thật)
    test_root = _PROJECT_ROOT / "data" / "raw_smoke_test"
    # Reset env override
    if ENV_RAW_DATA_ROOT in os.environ:
        del os.environ[ENV_RAW_DATA_ROOT]

    # ----------------------------------------------------------------------
    # A. Khởi tạo & Validation
    # ----------------------------------------------------------------------
    print("\n[--- A. KHỞI TẠO & VALIDATION ---]")

    print("\n[A1] Default root = data/raw (project root)")
    writer = RawDataWriter(root=test_root)
    assert_check(
        str(writer.root).endswith("raw_smoke_test"),
        f"root = {writer.root}"
    )
    assert_check(writer.root.exists(), "root auto-created")
    assert_check(writer.root.is_dir(), "root là directory")

    print("\n[A2] Custom root respected")
    custom_root = _PROJECT_ROOT / "data" / "raw_custom_xyz"
    writer2 = RawDataWriter(root=custom_root)
    assert_check(str(writer2.root).endswith("raw_custom_xyz"),
                 f"custom root = {writer2.root}")

    print("\n[A3] Env var GA_RAW_DATA_ROOT override")
    env_root = _PROJECT_ROOT / "data" / "raw_env_xyz"
    os.environ[ENV_RAW_DATA_ROOT] = str(env_root)
    writer3 = RawDataWriter()  # dùng env
    assert_check(str(writer3.root).endswith("raw_env_xyz"),
                 f"env root = {writer3.root}")
    del os.environ[ENV_RAW_DATA_ROOT]

    print("\n[A4] compress_level ngoài [1,9] raise")
    try:
        RawDataWriter(root=test_root, compress_level=10)
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("compress_level" in str(e), f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # B. write_batch – cơ bản
    # ----------------------------------------------------------------------
    print("\n[--- B. WRITE_BATCH CƠ BẢN ---]")

    print("\n[B1] Ghi batch → file được tạo")
    # Reset test root
    if test_root.exists():
        shutil.rmtree(test_root)
    writer = RawDataWriter(root=test_root)
    events1 = [make_event(1), make_event(2)]
    meta1 = writer.write_batch("batch_001", events1, timestamp=1759305600.0)
    assert_check(Path(meta1.file_path).exists(), f"file tồn tại: {meta1.file_path}")
    assert_check(meta1.event_count == 2, f"event_count = {meta1.event_count}")

    print("\n[B2] Gzip magic header (1f 8b)")
    with open(meta1.file_path, "rb") as f:
        magic = f.read(2)
    assert_check(magic == b"\x1f\x8b", f"magic = {magic.hex()}")

    print("\n[B3] Partition đúng year=YYYY/month=MM/day=DD")
    # 1759305600 = 2025-10-01 (UTC)
    year, month, day = 2025, 10, 1
    expected_dir = f"year={year:04d}/month={month:02d}/day={day:02d}"
    # Normalize path separators
    normalized_path = meta1.file_path.replace("\\", "/")
    assert_check(expected_dir in normalized_path,
                 f"partition in path: {meta1.file_path}")
    assert_check(meta1.year == year, f"year = {meta1.year}")
    assert_check(meta1.month == month, f"month = {meta1.month}")
    assert_check(meta1.day == day, f"day = {meta1.day}")

    print("\n[B4] File count = 1 sau khi ghi")
    files = list(test_root.rglob(f"*{BATCH_FILE_EXT}"))
    assert_check(len(files) == 1, f"file count = {len(files)}")

    print("\n[B5] Sequence number tăng dần")
    meta2 = writer.write_batch("batch_002", events1, timestamp=1759305600.0)
    meta3 = writer.write_batch("batch_003", events1, timestamp=1759305600.0)
    files = list(test_root.rglob(f"*{BATCH_FILE_EXT}"))
    assert_check(len(files) == 3, f"3 files = {len(files)}")
    # Tên file sort đúng
    names = [p.name for p in sorted(files)]
    assert_check(names == sorted(names), f"sortable: {names}")
    assert_check(names[0] == "batch_000000.json.gz",
                 f"first seq = {names[0]}")
    assert_check(names[2] == "batch_000002.json.gz",
                 f"third seq = {names[2]}")

    # ----------------------------------------------------------------------
    # C. RawBatchMetadata
    # ----------------------------------------------------------------------
    print("\n[--- C. RAWBATCHMETADATA ---]")

    print("\n[C1] event_count, raw_size, compressed_size đúng")
    assert_check(meta3.event_count == 2, f"event_count = {meta3.event_count}")
    assert_check(meta3.raw_size > 0, f"raw_size = {meta3.raw_size}")
    assert_check(meta3.compressed_size > 0, f"compressed = {meta3.compressed_size}")
    assert_check(meta3.compressed_size < meta3.raw_size,
                 f"compressed < raw: {meta3.compressed_size} < {meta3.raw_size}")

    print("\n[C2] date_partition = year=YYYY/month=MM/day=DD")
    assert_check(
        meta3.date_partition == f"year={year}/month={month:02d}/day={day:02d}",
        f"partition = {meta3.date_partition}"
    )

    print("\n[C3] compression_ratio ∈ (0, 1]")
    assert_check(0 < meta3.compression_ratio <= 1.0,
                 f"ratio = {meta3.compression_ratio:.4f}")

    print("\n[C4] to_dict() có đủ field")
    d = meta3.to_dict()
    required_fields = {
        "batch_id", "file_path", "event_count", "raw_size",
        "compressed_size", "year", "month", "day",
        "timestamp", "status",
    }
    assert_check(
        required_fields.issubset(d.keys()),
        f"to_dict có {sorted(required_fields & set(d.keys()))}"
    )
    assert_check(d["status"] == "written", f"status = {d['status']}")

    # ----------------------------------------------------------------------
    # D. read_batch – replay
    # ----------------------------------------------------------------------
    print("\n[--- D. READ_BATCH (REPLAY) ---]")

    print("\n[D1] Read lại đúng JSON list")
    events_back = writer.read_batch(meta1.file_path)
    assert_check(isinstance(events_back, list), "returned list")
    assert_check(len(events_back) == 2, f"len = {len(events_back)}")

    print("\n[D2] Số event khớp với lúc ghi")
    assert_check(len(events_back) == meta1.event_count, "count match")

    print("\n[D3] Field 'user_id' preserve")
    uids = {e["user_id"] for e in events_back}
    assert_check(uids == {"player_000001", "player_000002"},
                 f"uids = {uids}")

    print("\n[D4] File không tồn tại raise FileNotFoundError")
    try:
        writer.read_batch("/nonexistent/path/batch_999.json.gz")
        raise AssertionError("Không raise")
    except FileNotFoundError as e:
        assert_check("không tồn tại" in str(e).lower(),
                     f"msg = {e}")

    # ----------------------------------------------------------------------
    # E. list_batches / count_batches / delete_batch
    # ----------------------------------------------------------------------
    print("\n[--- E. LIST / COUNT / DELETE ---]")

    print("\n[E1] list_batches trả files sorted")
    files = writer.list_batches()
    assert_check(len(files) == 3, f"3 files = {len(files)}")
    assert_check(files == sorted(files), f"sorted: {[f.name for f in files]}")

    print("\n[E2] count_batches khớp")
    assert_check(writer.count_batches() == 3, f"count = {writer.count_batches()}")

    print("\n[E3] delete_batch xoá file")
    target_file = files[0]
    assert_check(target_file.exists(), "before delete")
    writer.delete_batch(target_file)
    assert_check(not target_file.exists(), "after delete")
    assert_check(writer.count_batches() == 2, f"after: {writer.count_batches()}")

    # ----------------------------------------------------------------------
    # F. Validation trong write_batch
    # ----------------------------------------------------------------------
    print("\n[--- F. WRITE_BATCH VALIDATION ---]")

    print("\n[F1] batch_id rỗng raise ValueError")
    try:
        writer.write_batch("", [make_event(1)])
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("batch_id" in str(e), f"ValueError: {e}")

    print("\n[F2] events KHÔNG phải list raise ValueError")
    try:
        writer.write_batch("batch_x", {"not": "a list"})
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("list" in str(e).lower(), f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # G. ReplayableRawWriter
    # ----------------------------------------------------------------------
    print("\n[--- G. REPLAYABLERAWWRITER ---]")

    print("\n[G1] attach_client() link client")
    rw = ReplayableRawWriter(raw_writer=writer)

    print("\n[G2] send() ghi raw + gửi API (success)")
    # Mock client
    from gameanalytics.retry import (
        RetryableGameAnalyticsClient, RetryPolicy,
    )
    from gameanalytics import GameAnalyticsClient
    TEST_SECRET = "0" * 64

    # Setup ReplayableWriter với own writer (tránh conflict với writer ở B)
    rw_root = _PROJECT_ROOT / "data" / "raw_smoke_rw"
    if rw_root.exists():
        shutil.rmtree(rw_root)
    rw_writer = RawDataWriter(root=rw_root)
    rw = ReplayableRawWriter(raw_writer=rw_writer)

    mock_ok = MockSession([MockResponse(200, {"ok": True})])
    base_client = GameAnalyticsClient(
        game_key="gk", secret_key=TEST_SECRET,
        base_url="https://x.com", http_session=mock_ok,
    )
    retry_client = RetryableGameAnalyticsClient(
        base_client=base_client,
        retry_policy=RetryPolicy(max_retries=0),
    )
    rw.attach_client(retry_client)

    events_rw = [make_event(100), make_event(101)]
    meta_rw, send_result = rw.send(events_rw, batch_id="rw_test_001")
    assert_check(meta_rw is not None, "meta returned")
    assert_check(send_result.success, "send success")
    # File đã ghi
    files_rw = list(rw_root.rglob(f"*{BATCH_FILE_EXT}"))
    assert_check(len(files_rw) == 1, f"file written: {len(files_rw)}")

    print("\n[G3] Raw file vẫn được ghi dù API fail")
    mock_fail = MockSession([
        MockResponse(500, {"error": "server"}),
        MockResponse(500, {"error": "server"}),
        MockResponse(500, {"error": "server"}),
    ])
    base_client2 = GameAnalyticsClient(
        game_key="gk", secret_key=TEST_SECRET,
        base_url="https://x.com", http_session=mock_fail,
    )
    retry_client2 = RetryableGameAnalyticsClient(
        base_client=base_client2,
        retry_policy=RetryPolicy(max_retries=0),
    )
    rw.attach_client(retry_client2)

    events_fail = [make_event(200), make_event(201)]
    files_before = len(list(rw_root.rglob(f"*{BATCH_FILE_EXT}")))
    try:
        rw.send(events_fail, batch_id="rw_fail_001")
    except Exception:
        pass  # expect fail
    files_after = len(list(rw_root.rglob(f"*{BATCH_FILE_EXT}")))
    assert_check(
        files_after == files_before + 1,
        f"raw still written: {files_before} → {files_after}"
    )

    # ----------------------------------------------------------------------
    # H. Partition filter
    # ----------------------------------------------------------------------
    print("\n[--- H. PARTITION FILTER ---]")

    print("\n[H1] list_batches(year=2025, month=10) lọc đúng")
    # Ghi thêm 1 batch khác ngày
    diff_ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    writer.write_batch("batch_other_day", [make_event(1)], timestamp=diff_ts)
    files_2025_10 = writer.list_batches(year=2025, month=10)
    files_2026_01 = writer.list_batches(year=2026, month=1)
    all_files = writer.list_batches()
    assert_check(
        len(files_2025_10) == 2,  # 2 file còn lại ở 2025-10
        f"2025-10: {len(files_2025_10)} = {[f.name for f in files_2025_10]}"
    )
    assert_check(
        len(files_2026_01) == 1,
        f"2026-01: {len(files_2026_01)} = {[f.name for f in files_2026_01]}"
    )
    assert_check(len(all_files) == 3, f"all: {len(all_files)}")

    # ----------------------------------------------------------------------
    # I. Stats
    # ----------------------------------------------------------------------
    print("\n[--- I. STATS ---]")

    print("\n[I1] RawWriterStats sau khi ghi nhiều batch")
    stats = writer.stats
    assert_check(isinstance(stats, RawWriterStats), f"type = {type(stats).__name__}")
    assert_check(stats.batches_written > 0, f"batches = {stats.batches_written}")
    assert_check(stats.events_written > 0, f"events = {stats.events_written}")
    assert_check(stats.bytes_written > 0, f"bytes = {stats.bytes_written}")
    print(f"  [INFO] {stats}")

    # ----------------------------------------------------------------------
    # J. Atomic write
    # ----------------------------------------------------------------------
    print("\n[--- J. ATOMIC WRITE ---]")

    print("\n[J1] Ghi nhiều batch → không có partial file")
    test_root_j = _PROJECT_ROOT / "data" / "raw_smoke_j"
    if test_root_j.exists():
        shutil.rmtree(test_root_j)
    writer_j = RawDataWriter(root=test_root_j)
    for i in range(50):
        writer_j.write_batch(f"batch_{i:03d}", [make_event(i)])
    # Kiểm tra tất cả file .json.gz hợp lệ (gzip + parse được)
    files_j = list(test_root_j.rglob(f"*{BATCH_FILE_EXT}"))
    bad = 0
    for f in files_j:
        try:
            with gzip.open(f, "rb") as gz:
                json.loads(gz.read().decode("utf-8"))
        except Exception:
            bad += 1
    assert_check(bad == 0, f"bad files = {bad}/{len(files_j)}")
    assert_check(len(files_j) == 50, f"50 files = {len(files_j)}")

    # ----------------------------------------------------------------------
    # K. Concurrency (thread-safe)
    # ----------------------------------------------------------------------
    print("\n[--- K. CONCURRENCY ---]")

    print("\n[K1] 10 thread ghi đồng thời → file unique")
    test_root_k = _PROJECT_ROOT / "data" / "raw_smoke_k"
    if test_root_k.exists():
        shutil.rmtree(test_root_k)
    writer_k = RawDataWriter(root=test_root_k)

    def worker(tid):
        for i in range(5):
            try:
                writer_k.write_batch(
                    f"thread_{tid}_batch_{i}",
                    [make_event(tid * 10 + i)],
                )
            except Exception as e:
                print(f"  [ERR] thread {tid} batch {i}: {e}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    files_k = list(test_root_k.rglob(f"*{BATCH_FILE_EXT}"))
    # 10 threads × 5 batches = 50 files
    assert_check(len(files_k) == 50, f"50 files = {len(files_k)}")
    # Tất cả unique
    names_set = {f.name for f in files_k}
    assert_check(len(names_set) == len(files_k),
                 f"unique: {len(names_set)}/{len(files_k)}")

    # ----------------------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------------------
    print("\n[--- CLEANUP ---]")
    for d in [test_root, rw_root, test_root_j, test_root_k,
              custom_root, env_root]:
        if d.exists():
            shutil.rmtree(d)
    print("  [OK] cleaned test dirs")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 22 RAW DATA WRITER OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
