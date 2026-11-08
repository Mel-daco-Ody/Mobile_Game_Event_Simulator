"""Event Batcher – gom event theo compressed size < 1 MB.

Theo BƯỚC 17 INSTRUCTION.md:

GameAnalytics có POST size limit = 1 MB. Batch KHÔNG được quá 1 MB
(compressed). Đặt SAFE_LIMIT thấp hơn để có margin.

Quy tắc:

    - GameAnalytics có POST size limit = 1 MB.
    - KHÔNG dùng ``batch_size = 10000`` cố định vì event size không đều.
    - Kiểm tra ``compressed_payload_size`` sau gzip.
    - Đặt ``SAFE_LIMIT`` thấp hơn 1 MB (ví dụ 900 KB) để có margin.

Pipeline::

    Events
      ↓ add_event
    Serialize JSON (list)
      ↓
    GZIP compression
      ↓
    Check size
      ↓
    < SAFE_LIMIT? → YES → add next
                  → NO  → flush current batch

> Không được load 20–30M events vào RAM. Phải Generate → Batch →
> Write → Send → Clear → lặp tiếp.

Cách hoạt động:

    Batcher hoạt động như một buffer tích luỹ. Mỗi ``add_event``:

    1. Append event vào current_events.
    2. Gzip serialize toàn batch.
    3. Nếu size > SAFE_LIMIT:
       - Pop event ra khỏi batch (giữ lại batch vừa có size OK)
       - Flush batch qua callback (hoặc return qua flush()).
       - Bắt đầu batch mới với event vừa pop.
    4. Nếu size <= SAFE_LIMIT: giữ batch và đợi add tiếp.

Memory:

    Batcher chỉ giữ 1 batch tại 1 thời điểm. Sau khi flush,
    current_events được clear. Phù hợp với pipeline
    Generate → Batch → Write → Send → Clear.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from config import game_config


# =========================================================================
# Batch dataclass
# =========================================================================

@dataclass
class Batch:
    """Một batch đã gom xong (sẵn sàng gửi).

    Attributes:
        events: list event trong batch (giữ reference, KHÔNG copy để tiết kiệm RAM).
        compressed_size: kích thước gzip của JSON list (bytes).
        raw_size: kích thước JSON list trước khi gzip (bytes).
    """

    events: list[dict[str, Any]]
    compressed_size: int
    raw_size: int

    @property
    def event_count(self) -> int:
        """Số event trong batch."""
        return len(self.events)

    @property
    def compression_ratio(self) -> float:
        """Tỉ số nén (compressed / raw). Càng nhỏ càng tốt.

        Returns:
            float ∈ (0, 1]. 1.0 = không nén được.
        """
        if self.raw_size == 0:
            return 1.0
        return self.compressed_size / self.raw_size

    def to_json_bytes(self) -> bytes:
        """Serialize JSON list (KHÔNG gzip) – dùng cho debug/log."""
        return json.dumps(
            self.events, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    def to_gzip_bytes(self) -> bytes:
        """Serialize + gzip batch (bytes) – body thật gửi qua HTTP.

        Delegate cho ``compressor.compress_events`` (single source of
        truth) để đảm bảo batcher output = client HMAC input.
        """
        from .compressor import compress_events
        return compress_events(self.events)


# =========================================================================
# Batcher
# =========================================================================

class Batcher:
    """Buffer gom event theo compressed size.

    Usage::

        batcher = Batcher()
        for event in event_generator:
            batcher.add_event(event)   # tự động flush khi đầy
        last_batch = batcher.flush()    # flush batch cuối (có thể rỗng)

    Với callback::

        batcher = Batcher(on_flush=lambda b: send_to_api(b.to_gzip_bytes()))
        for ev in events:
            batcher.add_event(ev)
        batcher.flush()

    Mỗi ``add_event``:
        - Append event.
        - Gzip serialize toàn batch.
        - Nếu vượt SAFE_LIMIT: pop event, flush batch cũ,
          bắt đầu batch mới với event đó.
        - Nếu 1 event đơn lẻ > SAFE_LIMIT: skip + flag ``oversized_dropped``.
    """

    def __init__(
        self,
        safe_limit_bytes: int = game_config.SAFE_LIMIT_BYTES,
        on_flush: Callable[[Batch], None] | None = None,
    ) -> None:
        """Khởi tạo batcher.

        Args:
            safe_limit_bytes: ngưỡng compressed size tối đa (mặc định
                              ``game_config.SAFE_LIMIT_BYTES = 900 KB``).
            on_flush: callback khi batch đầy. Nhận ``Batch``.
                      Nếu None, batch phải được lấy qua ``flush()``.
        """
        if safe_limit_bytes <= 0:
            raise ValueError(
                f"safe_limit_bytes phải > 0, nhận {safe_limit_bytes}"
            )
        if safe_limit_bytes > game_config.POST_LIMIT_BYTES:
            # Cảnh báo: safe_limit > post_limit là vô nghĩa
            raise ValueError(
                f"safe_limit_bytes ({safe_limit_bytes}) phải <= "
                f"POST_LIMIT_BYTES ({game_config.POST_LIMIT_BYTES}). "
                f"Ngược lại GameAnalytics sẽ reject."
            )
        self._safe_limit = safe_limit_bytes
        self._on_flush = on_flush
        self._current: list[dict[str, Any]] = []
        self._current_compressed_size: int = 0
        self._current_raw_size: int = 0
        # Stats
        self._stats = BatcherStats()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def safe_limit_bytes(self) -> int:
        """Ngưỡng compressed size hiện tại."""
        return self._safe_limit

    @property
    def current_event_count(self) -> int:
        """Số event hiện có trong current batch."""
        return len(self._current)

    @property
    def current_compressed_size(self) -> int:
        """Compressed size của current batch (bytes)."""
        return self._current_compressed_size

    @property
    def is_empty(self) -> bool:
        """Current batch rỗng."""
        return len(self._current) == 0

    @property
    def stats(self) -> "BatcherStats":
        """Thống kê batcher (batches_flushed, events_sent, oversized_dropped)."""
        return self._stats

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def add_event(self, event: dict[str, Any]) -> Batch | None:
        """Thêm 1 event vào batch hiện tại.

        Nếu batch vượt SAFE_LIMIT sau khi add:
            - Pop event vừa add ra.
            - Flush batch (gọi on_flush HOẶC trả về qua return).
            - Bắt đầu batch mới với event vừa pop.

        Nếu 1 event đơn lẻ > SAFE_LIMIT:
            - KHÔNG thêm vào batch.
            - Increment ``oversized_dropped`` counter.
            - Trả về None.

        Args:
            event: dict event đã được tạo bởi generator.

        Returns:
            - Batch nếu auto-flush xảy ra (và KHÔNG có on_flush).
            - None nếu event được add vào batch hiện tại
              HOẶC on_flush đã xử lý flush.
            - None nếu event bị drop (oversized).
        """
        # Thử add vào batch
        self._current.append(event)
        new_size, new_raw = self._compute_sizes()

        if new_size > self._safe_limit:
            # Vượt limit → pop event, flush batch (KHÔNG có event này)
            self._current.pop()
            flushed_batch = self._make_batch() if self._current else None

            # Tính size riêng cho event đơn lẻ
            single_size, _ = self._compute_sizes_single(event)
            if single_size > self._safe_limit:
                # 1 event > SAFE_LIMIT: KHÔNG thể gửi
                self._stats.oversized_dropped += 1
                # Trả về batch cũ (flush) hoặc None
                if flushed_batch is not None:
                    return self._dispatch_flush(flushed_batch)
                return None

            # Flush batch cũ
            if flushed_batch is not None:
                self._dispatch_flush(flushed_batch)
            # Reset và bắt đầu batch mới với event này
            self._reset_state()
            self._current.append(event)
            self._current_compressed_size, self._current_raw_size = \
                self._compute_sizes_single(event)
        else:
            # Vừa trong limit: cập nhật size
            self._current_compressed_size = new_size
            self._current_raw_size = new_raw
        return None

    def add_events(self, events: list[dict[str, Any]]) -> list[Batch]:
        """Thêm nhiều event. Trả về list các batch đã auto-flush."""
        flushed = []
        for ev in events:
            result = self.add_event(ev)
            if isinstance(result, Batch):
                flushed.append(result)
        return flushed

    def flush(self) -> Batch | None:
        """Flush batch hiện tại (nếu không rỗng).

        Returns:
            Batch nếu có event, None nếu rỗng.
            Nếu ``on_flush`` đã được set, batch sẽ được gửi qua callback
            và trả về None (callback chịu trách nhiệm xử lý).
        """
        if self.is_empty:
            return None
        batch = self._make_batch()
        self._reset_state()
        return self._dispatch_flush(batch)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_sizes(self) -> tuple[int, int]:
        """Tính (compressed_size, raw_size) của current batch.

        Delegate cho ``compressor.compress_events`` (single source of
        truth) để đảm bảo Batcher và Client dùng CÙNG gzip logic.
        Tránh bug: HMAC tính trên gzip bytes từ Client ≠ body gửi
        từ Batcher (nếu 2 chỗ dùng compresslevel khác nhau).
        """
        if not self._current:
            return 0, 0
        # Lazy import tránh circular
        from .compressor import compress_events, serialize_events
        compressed = compress_events(self._current)
        raw = serialize_events(self._current)
        return len(compressed), len(raw)

    @staticmethod
    def _compute_sizes_single(event: dict[str, Any]) -> tuple[int, int]:
        """Tính (compressed_size, raw_size) của 1 event riêng lẻ."""
        from .compressor import compress_events, serialize_events
        compressed = compress_events([event])
        raw = serialize_events([event])
        return len(compressed), len(raw)

    def _make_batch(self) -> Batch:
        """Tạo Batch từ current (KHÔNG copy event list)."""
        return Batch(
            events=self._current,
            compressed_size=self._current_compressed_size,
            raw_size=self._current_raw_size,
        )

    def _reset_state(self) -> None:
        """Reset current batch."""
        self._current = []
        self._current_compressed_size = 0
        self._current_raw_size = 0

    def _dispatch_flush(self, batch: Batch) -> Batch | None:
        """Xử lý flush: gọi callback hoặc return batch.

        Returns:
            - None nếu on_flush được set (callback xử lý).
            - Batch nếu không có on_flush (caller xử lý).
        """
        self._stats.batches_flushed += 1
        self._stats.events_sent += batch.event_count
        self._stats.bytes_compressed_sent += batch.compressed_size
        if self._on_flush is not None:
            self._on_flush(batch)
            return None
        return batch


# =========================================================================
# BatcherStats
# =========================================================================

@dataclass
class BatcherStats:
    """Thống kê batcher (cho monitoring)."""

    batches_flushed: int = 0
    events_sent: int = 0
    bytes_compressed_sent: int = 0
    oversized_dropped: int = 0

    @property
    def avg_events_per_batch(self) -> float:
        if self.batches_flushed == 0:
            return 0.0
        return self.events_sent / self.batches_flushed

    @property
    def avg_compressed_size_bytes(self) -> float:
        if self.batches_flushed == 0:
            return 0.0
        return self.bytes_compressed_sent / self.batches_flushed

    def __str__(self) -> str:
        return (
            f"BatcherStats(batches_flushed={self.batches_flushed}, "
            f"events_sent={self.events_sent}, "
            f"avg_events/batch={self.avg_events_per_batch:.1f}, "
            f"avg_compressed_size={self.avg_compressed_size_bytes:.0f}B, "
            f"oversized_dropped={self.oversized_dropped})"
        )


# =========================================================================
# Convenience: standalone flush helper
# =========================================================================

def batch_events_by_size(
    events: list[dict[str, Any]],
    safe_limit_bytes: int = game_config.SAFE_LIMIT_BYTES,
) -> list[Batch]:
    """Gom list event thành nhiều batch theo compressed size.

    Hàm tiện ích cho caller chỉ muốn batch 1 lần rồi xử lý.

    Args:
        events: list event.
        safe_limit_bytes: ngưỡng compressed size.

    Returns:
        list Batch (ít nhất 0 batch nếu events rỗng).
    """
    batcher = Batcher(safe_limit_bytes=safe_limit_bytes)
    batches: list[Batch] = []
    for ev in events:
        result = batcher.add_event(ev)
        if isinstance(result, Batch):
            batches.append(result)
    last = batcher.flush()
    if isinstance(last, Batch):
        batches.append(last)
    return batches


__all__ = [
    "Batcher",
    "Batch",
    "BatcherStats",
    "batch_events_by_size",
]
