"""Raw Data Writer – lưu raw batches độc lập với GameAnalytics.

Theo BƯỚC 22 INSTRUCTION.md:

    **Mục tiêu:** Lưu raw data **độc lập** với GameAnalytics
    để có thể replay.

    Tạo ``storage/raw_writer.py``.

    Cấu trúc thư mục::

        data/
        └── raw/
            └── year=YYYY/
                └── month=MM/
                    └── day=DD/
                        ├── batch_000001.json.gz
                        ├── batch_000002.json.gz
                        └── ...

    Mục đích::

        Simulator
          ├──→ GameAnalytics Collection API
          └──→ Local Raw Data Lake

        API gặp lỗi → Raw Data → Replay → Pipeline

Module cung cấp:

    - ``RawDataWriter``: ghi batch gzip + partition theo ngày
    - ``RawBatchMetadata``: thông tin 1 batch đã ghi
    - ``ReplayableRawWriter``: wrapper tích hợp ``Batch`` (Bước 17) +
      ``RetryableGameAnalyticsClient`` (Bước 21)
    - ``RawBatchStatus``: enum trạng thái (written/replayed/archived)

Storage partition (Hive-style)::

    /<root>/year=YYYY/month=MM/day=DD/batch_NNNNN.json.gz

Ưu điểm:
    - Partition theo ngày → query dễ (spark, hive, duckdb)
    - File .json.gz → đọc lại được bất cứ lúc nào (không cần format đặc biệt)
    - Atomic write: ghi temp → rename → tránh partial file
    - Thread-safe: dùng ``threading.Lock``
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


# =========================================================================
# Defaults
# =========================================================================

# Default root: <project_root>/data/raw
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_ROOT: Path = _PROJECT_ROOT / "data" / "raw"

# File naming pattern
BATCH_FILE_PREFIX: str = "batch_"
BATCH_FILE_EXT: str = ".json.gz"

# Env var override
ENV_RAW_DATA_ROOT: str = "GA_RAW_DATA_ROOT"

# Date format
DATE_FORMAT_DATE: str = "%Y-%m-%d"


class RawBatchStatus(str, Enum):
    """Trạng thái của 1 batch trong raw data lake."""

    WRITTEN = "written"      # Đã ghi ra file
    REPLAYED = "replayed"    # Đã replay lên GameAnalytics
    ARCHIVED = "archived"    # Đã archive (move sang cold storage)
    FAILED = "failed"        # Ghi thất bại


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class RawBatchMetadata:
    """Metadata của 1 batch đã ghi xong.

    Attributes:
        batch_id: định danh batch.
        file_path: đường dẫn file tuyệt đối.
        event_count: số event trong batch.
        raw_size: kích thước JSON trước gzip (bytes).
        compressed_size: kích thước sau gzip (bytes).
        year, month, day: partition keys.
        timestamp: unix timestamp lúc ghi.
        status: trạng thái (RawBatchStatus).
        extra: metadata bổ sung (vd: source, retry_count).
    """

    batch_id: str
    file_path: str
    event_count: int
    raw_size: int
    compressed_size: int
    year: int
    month: int
    day: int
    timestamp: float
    status: RawBatchStatus = RawBatchStatus.WRITTEN
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def date_partition(self) -> str:
        """Partition path tương đối (year=YYYY/month=MM/day=DD)."""
        return f"year={self.year:04d}/month={self.month:02d}/day={self.day:02d}"

    @property
    def compression_ratio(self) -> float:
        """Tỉ số nén."""
        if self.raw_size == 0:
            return 1.0
        return self.compressed_size / self.raw_size

    def to_dict(self) -> dict[str, Any]:
        """Serialize thành dict (cho log/index)."""
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class RawWriterStats:
    """Thống kê của RawDataWriter."""

    batches_written: int = 0
    batches_failed: int = 0
    bytes_written: int = 0
    events_written: int = 0

    def __str__(self) -> str:
        return (
            f"RawWriterStats(batches={self.batches_written}, "
            f"failed={self.batches_failed}, "
            f"events={self.events_written}, "
            f"bytes={self.bytes_written})"
        )


# =========================================================================
# RawDataWriter
# =========================================================================

class RawDataWriter:
    """Ghi raw batch ra disk với Hive-style partition.

    Usage::

        writer = RawDataWriter()  # root = data/raw
        meta = writer.write_batch("batch_001", events)
        print(meta.file_path)
        # → /path/to/data/raw/year=2026/month=09/day=28/batch_000001.json.gz

        # Đọc lại (replay)
        events = writer.read_batch(meta.file_path)
    """

    def __init__(
        self,
        root: str | Path | None = None,
        batch_id_generator: Callable[[], str] | None = None,
        compress_level: int = 6,
        time_source: Callable[[], float] | None = None,
        buffer_size: int = 1 << 20,  # 1 MB
    ) -> None:
        """Khởi tạo RawDataWriter.

        Args:
            root: thư mục gốc. Mặc định ``data/raw`` (relative to project root).
                  Có thể override qua env var ``GA_RAW_DATA_ROOT``.
            batch_id_generator: callable trả batch_id string. Mặc định
                                auto-generate ``batch_NNNNN``.
            compress_level: gzip level 1-9 (mặc định 6).
            time_source: callable trả float unix ts (cho test).
            buffer_size: buffer size cho file I/O.
        """
        if root is None:
            root = os.environ.get(ENV_RAW_DATA_ROOT, str(DEFAULT_RAW_ROOT))
        self.root: Path = Path(root).resolve()
        self._batch_id_gen = batch_id_generator or self._default_batch_id
        self._compress_level = compress_level
        self._time_source = time_source or self._default_time
        self._buffer_size = buffer_size

        # Validate compress_level
        if not 1 <= self._compress_level <= 9:
            raise ValueError(
                f"compress_level phải ∈ [1, 9], nhận {self._compress_level}"
            )

        # Thread safety
        self._lock = threading.Lock()

        # Stats
        self._stats = RawWriterStats()

        # Auto-create root khi write lần đầu
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def write_batch(
        self,
        batch_id: str,
        events: list[dict[str, Any]],
        timestamp: float | None = None,
    ) -> RawBatchMetadata:
        """Ghi 1 batch ra file gzip theo partition ngày.

        Args:
            batch_id: định danh batch (không rỗng).
            events: list event dict. PHẢI là list (per INSTRUCTION).
            timestamp: unix timestamp lúc ghi. None = dùng time_source.

        Returns:
            RawBatchMetadata với file_path, sizes, partition.

        Raises:
            ValueError: nếu batch_id rỗng hoặc events KHÔNG phải list.
            OSError: nếu ghi thất bại (permission, disk full...).
        """
        if not batch_id:
            raise ValueError("batch_id không được rỗng")
        if not isinstance(events, list):
            raise ValueError(
                f"events phải là list, nhận {type(events).__name__}"
            )

        ts = timestamp if timestamp is not None else self._time_source()
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        year, month, day = dt.year, dt.month, dt.day

        # Serialize → gzip
        raw_bytes = self._serialize_events(events)
        raw_size = len(raw_bytes)
        compressed = self._compress(raw_bytes)
        compressed_size = len(compressed)

        # Partition path
        partition_dir = (
            self.root / f"year={year:04d}" / f"month={month:02d}" / f"day={day:02d}"
        )

        with self._lock:
            partition_dir.mkdir(parents=True, exist_ok=True)

            # Filename: batch_NNNNN.json.gz (5 chữ số, sort được)
            seq = self._next_sequence_in_partition(partition_dir)
            filename = f"{BATCH_FILE_PREFIX}{seq:06d}.json.gz"
            file_path = partition_dir / filename

            # Atomic write: ghi temp → rename
            self._atomic_write(file_path, compressed)

            # Stats
            self._stats.batches_written += 1
            self._stats.events_written += len(events)
            self._stats.bytes_written += compressed_size

        return RawBatchMetadata(
            batch_id=batch_id,
            file_path=str(file_path),
            event_count=len(events),
            raw_size=raw_size,
            compressed_size=compressed_size,
            year=year,
            month=month,
            day=day,
            timestamp=ts,
            status=RawBatchStatus.WRITTEN,
        )

    def read_batch(self, file_path: str | Path) -> list[dict[str, Any]]:
        """Đọc lại batch từ file gzip → list event.

        Dùng cho replay khi API lỗi.

        Args:
            file_path: đường dẫn file tuyệt đối.

        Returns:
            list event dict.

        Raises:
            FileNotFoundError: nếu file không tồn tại.
            ValueError: nếu file không phải gzip hợp lệ.
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Raw batch file không tồn tại: {p}")
        with gzip.open(p, "rb") as f:
            raw = f.read()
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, list):
            raise ValueError(
                f"Raw batch phải là JSON list, nhận {type(parsed).__name__}"
            )
        return parsed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def list_batches(
        self,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> list[Path]:
        """List file batch trong partition (filter theo ngày nếu có).

        Args:
            year: filter theo năm (None = tất cả).
            month: filter theo tháng (None = tất cả).
            day: filter theo ngày (None = tất cả).

        Returns:
            list Path (sort theo tên file).
        """
        if year is None:
            # List tất cả
            return sorted(self.root.rglob(f"*{BATCH_FILE_EXT}"))

        y_dir = self.root / f"year={year:04d}"
        if not y_dir.exists():
            return []
        if month is None:
            return sorted(y_dir.rglob(f"*{BATCH_FILE_EXT}"))

        m_dir = y_dir / f"month={month:02d}"
        if not m_dir.exists():
            return []
        if day is None:
            return sorted(m_dir.rglob(f"*{BATCH_FILE_EXT}"))

        d_dir = m_dir / f"day={day:02d}"
        if not d_dir.exists():
            return []
        return sorted(d_dir.glob(f"*{BATCH_FILE_EXT}"))

    def count_batches(
        self,
        year: int | None = None,
        month: int | None = None,
        day: int | None = None,
    ) -> int:
        """Đếm số batch files trong partition."""
        return len(self.list_batches(year=year, month=month, day=day))

    def delete_batch(self, file_path: str | Path) -> None:
        """Xoá 1 batch file (sau khi replay thành công).

        Args:
            file_path: đường dẫn file cần xoá.

        Raises:
            FileNotFoundError: nếu file không tồn tại.
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Không tìm thấy: {p}")
        p.unlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _serialize_events(events: list[dict[str, Any]]) -> bytes:
        """Serialize list event → JSON bytes (delegate cho compressor)."""
        from gameanalytics.compressor import serialize_events
        return serialize_events(events)

    def _compress(self, raw_bytes: bytes) -> bytes:
        """Gzip với compress level đã cấu hình.

        Validate compress_level được check ở ``__init__``.
        """
        return gzip.compress(raw_bytes, compresslevel=self._compress_level)

    def _atomic_write(self, target: Path, data: bytes) -> None:
        """Ghi bytes an toàn: temp file → atomic rename.

        Tránh partial file nếu crash giữa chừng.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        # Tạo temp file cùng partition
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=".tmp_raw_",
            suffix=".json.gz",
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync không luôn khả dụng (vd: Windows)
                    pass
            # Atomic rename
            os.replace(tmp_path, target)
        except Exception:
            # Cleanup temp nếu rename fail
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:  # noqa: BLE001
                    pass
            raise

    def _next_sequence_in_partition(self, partition_dir: Path) -> int:
        """Tìm số sequence tiếp theo trong partition.

        Số sequence dựa trên số file batch_*.json.gz đã tồn tại + 1.
        """
        existing = list(partition_dir.glob(f"{BATCH_FILE_PREFIX}*{BATCH_FILE_EXT}"))
        max_seq = -1
        for p in existing:
            # Parse "batch_NNNNNN.json.gz"
            try:
                stem = p.stem  # "batch_NNNNNN"
                num_str = stem.replace(BATCH_FILE_PREFIX, "").replace(".json", "")
                seq = int(num_str)
                if seq > max_seq:
                    max_seq = seq
            except (ValueError, AttributeError):
                continue
        return max_seq + 1

    # ------------------------------------------------------------------
    # Default helpers (DI-friendly)
    # ------------------------------------------------------------------
    @staticmethod
    def _default_batch_id() -> str:
        """Default batch_id generator (timestamp-based)."""
        return f"batch_{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}"

    @staticmethod
    def _default_time() -> float:
        """Default time source."""
        return datetime.now(tz=timezone.utc).timestamp()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    @property
    def stats(self) -> RawWriterStats:
        """Thống kê của writer."""
        return self._stats


# =========================================================================
# ReplayableRawWriter – wrapper tích hợp Batch + RetryableClient
# =========================================================================

class ReplayableRawWriter:
    """Wrapper: ghi raw data AND gửi lên GameAnalytics.

    Pipeline::

        events
          ↓
        ReplayableRawWriter.send(events, batch_id)
          ├──→ raw_writer.write_batch()    ← lưu local trước (data quality)
          └──→ client.send_events()         ← gửi GameAnalytics

    Nếu gửi API thất bại: raw data vẫn ở local → có thể replay sau.

    Usage::

        from gameanalytics import (
            GameAnalyticsClient, RetryableGameAnalyticsClient,
            RetryPolicy,
        )
        from storage.raw_writer import ReplayableRawWriter

        base = GameAnalyticsClient.from_env()
        retry_client = RetryableGameAnalyticsClient(
            base_client=base,
            retry_policy=RetryPolicy(max_retries=5),
        )
        writer = ReplayableRawWriter(raw_writer=RawDataWriter())
        rw = writer.attach_client(retry_client)

        result = rw.send(events)
        # raw file đã save ở data/raw/year=YYYY/.../batch_NNNNNN.json.gz
        # Nếu fail → dùng replay() sau
    """

    def __init__(
        self,
        raw_writer: RawDataWriter | None = None,
    ) -> None:
        self.raw_writer = raw_writer or RawDataWriter()
        self._client: Any = None  # RetryableGameAnalyticsClient

    def attach_client(self, client: Any) -> "ReplayableRawWriter":
        """Attach client (RetryableGameAnalyticsClient)."""
        self._client = client
        return self

    def send(
        self,
        events: list[dict[str, Any]],
        batch_id: str | None = None,
        use_gzip: bool = True,
    ) -> tuple[RawBatchMetadata, Any]:
        """Ghi raw + gửi API.

        Args:
            events: list event.
            batch_id: nếu None, tự generate.
            use_gzip: gửi gzip mode.

        Returns:
            Tuple (RawBatchMetadata, SendResult).
            RawBatchMetadata LUÔN được return (kể cả khi send fail),
            để caller có file để replay.

        Raises:
            GameAnalyticsError: nếu send fail sau khi retry exhausted.
        """
        if self._client is None:
            raise RuntimeError(
                "Client chưa được attach. Gọi attach_client() trước."
            )

        # 1. Ghi raw trước (data quality guarantee)
        _batch_id = batch_id or self.raw_writer._batch_id_gen()
        meta = self.raw_writer.write_batch(_batch_id, events)

        # 2. Gửi API (retry nếu cần)
        send_result = self._client.send_events(
            events, batch_id=_batch_id, use_gzip=use_gzip,
        )

        return meta, send_result

    def replay(self, file_path: str | Path) -> Any:
        """Replay 1 raw batch file lên GameAnalytics.

        Args:
            file_path: file gzip đã lưu.

        Returns:
            SendResult.

        Raises:
            RuntimeError: nếu chưa attach client.
            FileNotFoundError: nếu file không tồn tại.
            GameAnalyticsError: nếu send fail.
        """
        if self._client is None:
            raise RuntimeError(
                "Client chưa được attach. Gọi attach_client() trước."
            )
        events = self.raw_writer.read_batch(file_path)
        # Dùng batch_id từ filename
        batch_id = Path(file_path).stem.replace(".json", "")
        return self._client.send_events(events, batch_id=batch_id)


# =========================================================================
# Convenience: utility functions (cho test + ad-hoc usage)
# =========================================================================

def write_raw_batch(
    events: list[dict[str, Any]],
    batch_id: str | None = None,
    root: str | Path | None = None,
) -> RawBatchMetadata:
    """Shortcut: tạo writer + ghi 1 batch.

    Args:
        events: list event.
        batch_id: batch id (None = auto).
        root: thư mục gốc (None = default).

    Returns:
        RawBatchMetadata.
    """
    writer = RawDataWriter(root=root)
    if batch_id is None:
        batch_id = writer._batch_id_gen()
    return writer.write_batch(batch_id, events)


def read_raw_batch(file_path: str | Path) -> list[dict[str, Any]]:
    """Shortcut: tạo writer + đọc 1 batch."""
    writer = RawDataWriter()
    return writer.read_batch(file_path)


__all__ = [
    "BATCH_FILE_EXT",
    "BATCH_FILE_PREFIX",
    "DEFAULT_RAW_ROOT",
    "ENV_RAW_DATA_ROOT",
    "RawBatchMetadata",
    "RawBatchStatus",
    "RawDataWriter",
    "RawWriterStats",
    "ReplayableRawWriter",
    "read_raw_batch",
    "write_raw_batch",
]
