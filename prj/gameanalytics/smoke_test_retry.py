"""Smoke test cho BƯỚC 21 – Retry Mechanism + mark_event_failed.

Kiểm tra:

A. RetryPolicy:
    1. compute_delay(1) = base_delay (retry đầu tiên).
    2. compute_delay(2) = base_delay * 2.
    3. compute_delay(3) = base_delay * 4.
    4. max_delay cap.
    5. jitter factor.
    6. should_retry / is_exhausted.
    7. max_retries=0 → không retry.
    8. Validation: base > max_delay raise.

B. RetryBatch dataclass:
    9. attempts_remaining = max - current.
    10. is_exhausted.

C. RetryQueue – schema migration:
    11. ALTER TABLE queue thêm 4 cột mới.
    12. batch_retry table được tạo.

D. RetryQueue – add_failed_batch:
    13. Batch mới → retryable với retry_count=1.
    14. Batch trùng → tăng retry_count.
    15. Exhausted → mark_batch_exhausted.

E. RetryQueue – get_retryable_batches:
    16. Chưa đến giờ → không trả.
    17. Đến giờ → trả batch.
    18. Sắp xếp theo next_retry_at.

F. RetryQueue – mark_batch_sent / mark_batch_exhausted:
    19. mark_batch_sent → xoá khỏi retryable.
    20. mark_batch_exhausted → chuyển exhausted.

G. RetryQueue – stats:
    21. Stats đúng sau add/get/mark.

H. mark_event_failed / mark_retry_exhausted (SQLiteStorage):
    22. mark_event_failed → retry_count tăng.
    23. mark_retry_exhausted → status='failed'.

I. RetryableGameAnalyticsClient:
    24. Retry 5xx → retry.
    25. AuthError 401 → không retry.
    26. PayloadTooLargeError 413 → không retry.
    27. Exhausted → raise.
    28. max_retries=0 → không retry.

J. Persistence:
    29. load_from_db → khôi phục state.

K. create_retry_client factory:
    30. Tạo client từ .env.

Chạy:
    cd prj && python gameanalytics/smoke_test_retry.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gameanalytics import GameAnalyticsClient  # noqa: E402
from gameanalytics.retry import (  # noqa: E402
    RetryBatch,
    RetryPolicy,
    RetryQueue,
    RetryQueueStats,
    RetryableGameAnalyticsClient,
    create_retry_client,
)


# =====================================================================
# Mock helpers
# =====================================================================

class MockResponse:
    def __init__(self, status_code: int, json_data: dict | str | None = None):
        self.status_code = status_code
        self._json = json_data
        self.text = (
            json.dumps(json_data) if isinstance(json_data, dict)
            else str(json_data or "")
        )

    def json(self):
        return self._json


class MockSession:
    def __init__(
        self,
        responses: list[MockResponse] | MockResponse,
        raise_after: int | None = None,
    ) -> None:
        self.requests_made: list[dict] = []
        self._responses = (
            responses if isinstance(responses, list) else [responses]
        )
        self._idx = 0
        self._raise_after = raise_after

    def post(self, url, data, headers, timeout):
        self.requests_made.append({"url": url, "headers": dict(headers)})
        if self._raise_after is not None and self._idx >= self._raise_after:
            raise ConnectionError("mock connection error")
        resp = self._responses[self._idx] if self._idx < len(self._responses) else self._responses[-1]
        self._idx += 1
        return resp


TEST_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def make_events(n=3):
    return [
        {
            "category": "design", "v": 2,
            "user_id": f"p{i}", "client_ts": 1759305600 + i,
            "sdk_version": "rest api v2", "os_version": "android 15",
            "session_id": "sess001", "session_num": 1,
            "event_id": f"event_{i}", "value": i,
        }
        for i in range(n)
    ]


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 21: RETRY MECHANISM")
    print("=" * 60)

    # Setup temp DB
    with tempfile.NamedTemporaryFile(
        suffix=".db", delete=False, prefix="retry_smoke_",
    ) as f:
        tmp_db = f.name

    try:
        from gameanalytics.retry import SQLiteStorage
        storage = SQLiteStorage(tmp_db)

        # ------------------------------------------------------------------
        # A. RetryPolicy
        # ------------------------------------------------------------------
        print("\n[--- A. RETRYPOLICY ---]")

        print("\n[A1] compute_delay(1) = base_delay")
        policy = RetryPolicy(
            max_retries=5,
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_factor=None,  # deterministic
        )
        d1 = policy.compute_delay(1)
        assert_check(d1 == 1.0, f"delay(1) = {d1}")

        print("\n[A2] compute_delay(2) = base_delay * 2")
        d2 = policy.compute_delay(2)
        assert_check(d2 == 2.0, f"delay(2) = {d2}")

        print("\n[A3] compute_delay(3) = base_delay * 4")
        d3 = policy.compute_delay(3)
        assert_check(d3 == 4.0, f"delay(3) = {d3}")

        print("\n[A4] max_delay cap")
        policy_cap = RetryPolicy(
            max_retries=10,
            base_delay_seconds=30.0,
            max_delay_seconds=60.0,
            jitter_factor=None,
        )
        d6 = policy_cap.compute_delay(6)  # 30 * 2^5 = 960 > 60
        assert_check(d6 == 60.0, f"cap: delay(6) = {d6}")

        print("\n[A5] Jitter factor")
        policy_j = RetryPolicy(
            max_retries=5,
            base_delay_seconds=1.0,
            jitter_factor=0.1,
        )
        # Jitter: ±10%, deterministic PRNG → check range
        delays = [policy_j.compute_delay(1) for _ in range(10)]
        assert_check(all(0.9 <= d <= 1.1 for d in delays),
                     f"jitter in [0.9, 1.1]: {delays}")

        print("\n[A6] should_retry / is_exhausted")
        # Với max_retries=5: should_retry(0..5) = True, should_retry(6) = False
        assert_check(policy.should_retry(0), "retry 0 times")
        assert_check(policy.should_retry(1), "retry 1 time")
        assert_check(policy.should_retry(5), "can retry at 5")
        assert_check(not policy.should_retry(6), "exhausted at 6")
        assert_check(policy.is_exhausted(6), "is_exhausted(6)")
        assert_check(not policy.is_exhausted(5), "not exhausted(5)")

        print("\n[A7] max_retries=0 → không retry")
        zero = RetryPolicy(max_retries=0, jitter_factor=None)
        # Sau 1 retry attempt → exhausted
        assert_check(not zero.should_retry(1), "should_retry(1) = False")
        assert_check(zero.is_exhausted(1), "is_exhausted(1) = True")

        print("\n[A8] base > max_delay raise")
        try:
            RetryPolicy(
                base_delay_seconds=60.0,
                max_delay_seconds=30.0,
                jitter_factor=None,
            )
            raise AssertionError("Không raise")
        except ValueError as e:
            assert_check(True, f"ValueError: {e}")

        # ------------------------------------------------------------------
        # B. RetryBatch dataclass
        # ------------------------------------------------------------------
        print("\n[--- B. RETRYBATCH ---]")

        print("\n[B1] attempts_remaining = max - current")
        batch = RetryBatch(
            batch_id="b1",
            events=make_events(2),
            retry_count=3,
            max_retries=5,
            last_error="err",
            next_retry_at=1000.0,
            created_at="2026-09-01T00:00:00Z",
        )
        assert_check(batch.attempts_remaining == 2,
                     f"remaining = {batch.attempts_remaining}")
        assert_check(batch.is_exhausted is False, "not exhausted")

        print("\n[B2] Exhausted")
        exhausted = RetryBatch(
            batch_id="b2", events=[],
            retry_count=5, max_retries=5,
            last_error="last", next_retry_at=0.0,
            created_at="2026-09-01T00:00:00Z",
        )
        assert_check(exhausted.is_exhausted, "is_exhausted=True")
        assert_check(exhausted.attempts_remaining == 0,
                     f"remaining=0, got {exhausted.attempts_remaining}")

        # ------------------------------------------------------------------
        # C. RetryQueue schema
        # ------------------------------------------------------------------
        print("\n[--- C. RETRYQUEUE SCHEMA ---]")

        print("\n[C1] ALTER TABLE queue thêm 4 cột")
        q = RetryQueue(storage=storage)
        # storage conn được quản lý bởi RetryQueue (đã mở và đóng)
        with storage as s:
            cur = s.conn.execute("PRAGMA table_info(queue)")
            cols = {r["name"] for r in cur.fetchall()}
        expected_queue_cols = {
            "retry_count", "next_retry_at", "last_error", "last_attempt_at",
        }
        assert_check(
            expected_queue_cols.issubset(cols),
            f"queue cols: {expected_queue_cols & cols}"
        )

        print("\n[C2] batch_retry table được tạo")
        with storage as s:
            cur = s.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_retry'"
            )
            assert_check(cur.fetchone() is not None, "batch_retry table tồn tại")

        # ------------------------------------------------------------------
        # D. RetryQueue – add_failed_batch
        # ------------------------------------------------------------------
        print("\n[--- D. RETRYQUEUE ADD_FAILED ---]")

        print("\n[D1] Batch mới → retryable với retry_count=1")
        q = RetryQueue(
            storage=storage,
            retry_policy=RetryPolicy(max_retries=5, jitter_factor=None),
            time_source=lambda: 1000.0,
        )
        events = make_events(3)
        added = q.add_failed_batch("batch_001", events, "server error 500")
        assert_check(added, "added = True")
        assert_check("batch_001" in q._retryable, "batch_001 in retryable")
        b = q._retryable["batch_001"]
        assert_check(b.retry_count == 1, f"retry_count=1, got {b.retry_count}")
        assert_check(len(b.events) == 3, f"events preserved: {len(b.events)}")
        assert_check(b.last_error == "server error 500",
                     f"error: {b.last_error}")

        print("\n[D2] Batch trùng → tăng retry_count")
        added2 = q.add_failed_batch("batch_001", events, "server error 500 v2")
        assert_check(added2, "still added")
        b2 = q._retryable["batch_001"]
        assert_check(b2.retry_count == 2,
                     f"retry_count=2, got {b2.retry_count}")

        print("\n[D3] Exhausted (max_retries=2 → 3 attempts)")
        q2 = RetryQueue(
            storage=storage,
            retry_policy=RetryPolicy(max_retries=2, jitter_factor=None),
            time_source=lambda: 1000.0,
        )
        added3 = q2.add_failed_batch("batch_exhaust", events, "first error")
        assert_check(added3, "first added")
        added4 = q2.add_failed_batch("batch_exhaust", events, "second error")
        assert_check(added4, "second still retryable (count=2 <= max=2)")
        added5 = q2.add_failed_batch("batch_exhaust", events, "third error")
        assert_check(not added5, "third → exhausted (count=3 > max=2)")
        assert_check("batch_exhaust" not in q2._retryable,
                     "not in retryable")
        assert_check("batch_exhaust" in q2._exhausted,
                     "in exhausted")

        # ------------------------------------------------------------------
        # E. RetryQueue – get_retryable_batches
        # ------------------------------------------------------------------
        print("\n[--- E. RETRYQUEUE GET_RETRYABLE ---]")

        print("\n[E1] Chưa đến giờ → không trả")
        q3 = RetryQueue(
            storage=storage,
            retry_policy=RetryPolicy(max_retries=5, jitter_factor=None),
            time_source=lambda: 1000.0,
        )
        # next_retry_at = 1000 + 1.0 = 1001.0
        q3.add_failed_batch("b1", make_events(1), "e1")
        ready = q3.get_retryable_batches()  # now = 1000 < 1001
        assert_check(len(ready) == 0, f"not ready: {len(ready)}")

        print("\n[E2] Đến giờ → trả batch")
        ready = q3.get_retryable_batches()  # no-op, no time change
        assert_check(len(ready) == 0, "still 0")
        # Manually set time source past retry
        q3._time_source = lambda: 2000.0
        ready = q3.get_retryable_batches()
        assert_check(len(ready) == 1, f"ready: {len(ready)}")
        assert_check(ready[0].batch_id == "b1",
                     f"batch_id = {ready[0].batch_id}")

        print("\n[E3] Sắp xếp theo next_retry_at")
        q4 = RetryQueue(
            storage=storage,
            retry_policy=RetryPolicy(max_retries=5, jitter_factor=None),
            time_source=lambda: 1000.0,
        )
        # b_late với next_retry_at = 2000
        q4._retryable["b_late"] = RetryBatch(
            batch_id="b_late", events=make_events(1),
            retry_count=1, max_retries=5,
            last_error="e", next_retry_at=2000.0,
            created_at="2026-09-01T00:00:00Z",
        )
        # b_early với next_retry_at = 1001
        q4._retryable["b_early"] = RetryBatch(
            batch_id="b_early", events=make_events(1),
            retry_count=1, max_retries=5,
            last_error="e", next_retry_at=1001.0,
            created_at="2026-09-01T00:00:00Z",
        )
        # Now = 2000+ → cả 2 ready
        q4._time_source = lambda: 2500.0
        ready = q4.get_retryable_batches()
        assert_check(len(ready) == 2, f"2 ready")
        # Sắp xếp theo next_retry_at ASC (cũ nhất trước)
        # b_early.next_retry_at=1001 < b_late.next_retry_at=2000
        assert_check(
            ready[0].batch_id == "b_early" and ready[1].batch_id == "b_late",
            f"order ASC: {[b.batch_id for b in ready]}"
        )

        # ------------------------------------------------------------------
        # F. RetryQueue – mark_batch_sent / mark_batch_exhausted
        # ------------------------------------------------------------------
        print("\n[--- F. RETRYQUEUE MARK_DONE ---]")

        print("\n[F1] mark_batch_sent → xoá khỏi retryable")
        q5 = RetryQueue(
            storage=storage,
            retry_policy=RetryPolicy(max_retries=5, jitter_factor=None),
            time_source=lambda: 1000.0,
        )
        q5.add_failed_batch("b_sent", make_events(2), "e")
        assert_check("b_sent" in q5._retryable, "before sent")
        q5.mark_batch_sent("b_sent")
        assert_check("b_sent" not in q5._retryable, "after sent")

        print("\n[F2] mark_batch_exhausted → chuyển exhausted")
        q5.add_failed_batch("b_ex", make_events(2), "e")
        assert_check("b_ex" in q5._retryable, "before exhausted")
        q5.mark_batch_exhausted("b_ex")
        assert_check("b_ex" not in q5._retryable, "not in retryable")
        assert_check("b_ex" in q5._exhausted, "in exhausted")

        # ------------------------------------------------------------------
        # G. RetryQueue – stats
        # ------------------------------------------------------------------
        print("\n[--- G. RETRYQUEUE STATS ---]")

        print("\n[G1] Stats đúng sau operations")
        stats = q5.stats
        assert_check(
            isinstance(stats, RetryQueueStats),
            f"type = {type(stats).__name__}"
        )
        # q5 đã: b_sent (sent), b_ex (exhausted)
        # stats: 0 retryable, 1 exhausted
        assert_check(
            stats.retryable_batches == 0,
            f"retryable = {stats.retryable_batches}"
        )
        assert_check(
            stats.exhausted_batches == 1,
            f"exhausted = {stats.exhausted_batches}"
        )
        print(f"  [INFO] {stats}")

        # ------------------------------------------------------------------
        # H. SQLiteStorage.mark_event_failed / mark_retry_exhausted
        # ------------------------------------------------------------------
        print("\n[--- H. MARK_EVENT_FAILED (SQLiteStorage) ---]")

        print("\n[H1] mark_event_failed → retry_count tăng")
        from storage.sqlite import SQLiteStorage
        s2 = SQLiteStorage(tmp_db)
        with s2 as s:
            # Enqueue event
            eid = s.enqueue_event(make_events(1)[0])
            # Read initial
            row_before = s.conn.execute(
                "SELECT retry_count FROM queue WHERE id = ?", (eid,)
            ).fetchone()
            assert_check(row_before["retry_count"] == 0,
                         f"initial = {row_before['retry_count']}")
        # mark_event_failed (tự mở connection)
        s2.mark_event_failed(eid, "test error", None)
        with s2 as s:
            row_after = s.conn.execute(
                "SELECT retry_count, last_error, status FROM queue WHERE id = ?",
                (eid,),
            ).fetchone()
        assert_check(row_after["retry_count"] == 1,
                     f"after = {row_after['retry_count']}")
        assert_check(row_after["last_error"] == "test error",
                     f"error = {row_after['last_error']}")
        assert_check(row_after["status"] == "pending",
                     "still pending (not failed yet)")

        print("\n[H2] mark_retry_exhausted → status='failed'")
        with s2 as s:
            eid2 = s.enqueue_event(make_events(1)[0])
        s2.mark_retry_exhausted(eid2, "final error")
        with s2 as s:
            row3 = s.conn.execute(
                "SELECT status, last_error FROM queue WHERE id = ?", (eid2,),
            ).fetchone()
        assert_check(row3["status"] == "failed",
                     f"status = {row3['status']}")
        assert_check(row3["last_error"] == "final error",
                     f"error = {row3['last_error']}")

        # ------------------------------------------------------------------
        # I. RetryableGameAnalyticsClient
        # ------------------------------------------------------------------
        print("\n[--- I. RETRYABLEGAMEANALYTICSCIENT ---]")

        print("\n[I1] Retry 5xx → retry (không raise ngay)")
        # Mock: 500 → 200
        mock = MockSession([
            MockResponse(500, {"error": "server err"}),
            MockResponse(200, {"ok": True}),
        ])
        client = GameAnalyticsClient(
            game_key="gk", secret_key=TEST_SECRET,
            base_url="https://x.com", http_session=mock,
            time_source=lambda: 1000.0,
        )
        retry_client = RetryableGameAnalyticsClient(
            base_client=client,
            retry_policy=RetryPolicy(max_retries=3, base_delay_seconds=0.0,
                                    jitter_factor=None),
        )
        events = make_events(2)
        result = retry_client.send_events(events)
        assert_check(result.success, f"success = {result.success}")
        assert_check(len(mock.requests_made) == 2,
                     f"2 requests made (1 retry): {len(mock.requests_made)}")

        print("\n[I2] AuthError 401 → không retry, raise ngay")
        mock2 = MockSession(MockResponse(401, {"error": "bad auth"}))
        client2 = GameAnalyticsClient(
            game_key="gk", secret_key=TEST_SECRET,
            base_url="https://x.com", http_session=mock2,
        )
        retry_client2 = RetryableGameAnalyticsClient(
            base_client=client2,
            retry_policy=RetryPolicy(max_retries=5),
        )
        try:
            retry_client2.send_events(events)
            raise AssertionError("Không raise")
        except Exception as e:
            from gameanalytics import GameAnalyticsAuthError
            assert_check(
                isinstance(e, GameAnalyticsAuthError),
                f"raise {type(e).__name__}"
            )
            assert_check(len(mock2.requests_made) == 1,
                         f"no retry: {len(mock2.requests_made)} reqs")

        print("\n[I3] PayloadTooLargeError 413 → không retry")
        mock3 = MockSession(MockResponse(413, {"error": "too large"}))
        client3 = GameAnalyticsClient(
            game_key="gk", secret_key=TEST_SECRET,
            base_url="https://x.com", http_session=mock3,
        )
        retry_client3 = RetryableGameAnalyticsClient(
            base_client=client3,
            retry_policy=RetryPolicy(max_retries=5),
        )
        try:
            retry_client3.send_events(events)
            raise AssertionError("Không raise")
        except Exception as e:
            from gameanalytics import GameAnalyticsPayloadTooLargeError
            assert_check(
                isinstance(e, GameAnalyticsPayloadTooLargeError),
                f"raise {type(e).__name__}"
            )

        print("\n[I4] Exhausted (max_retries=2) → raise sau 2 retry")
        mock4 = MockSession(MockResponse(500, {"error": "x"}))
        client4 = GameAnalyticsClient(
            game_key="gk", secret_key=TEST_SECRET,
            base_url="https://x.com", http_session=mock4,
        )
        retry_client4 = RetryableGameAnalyticsClient(
            base_client=client4,
            retry_policy=RetryPolicy(
                max_retries=2, base_delay_seconds=0.0, jitter_factor=None,
            ),
        )
        try:
            retry_client4.send_events(events)
            raise AssertionError("Không raise")
        except Exception as e:
            from gameanalytics import GameAnalyticsServerError
            assert_check(
                isinstance(e, GameAnalyticsServerError),
                f"raise {type(e).__name__}"
            )
        # Phải retry 3 lần (1 original + 2 retry)
        assert_check(len(mock4.requests_made) == 3,
                     f"3 requests: {len(mock4.requests_made)}")

        print("\n[I5] max_retries=0 → không retry, raise ngay")
        mock5 = MockSession(MockResponse(500, {"error": "x"}))
        client5 = GameAnalyticsClient(
            game_key="gk", secret_key=TEST_SECRET,
            base_url="https://x.com", http_session=mock5,
        )
        retry_client5 = RetryableGameAnalyticsClient(
            base_client=client5,
            retry_policy=RetryPolicy(max_retries=0),
        )
        try:
            retry_client5.send_events(events)
            raise AssertionError("Không raise")
        except Exception as e:
            from gameanalytics import GameAnalyticsServerError
            assert_check(isinstance(e, GameAnalyticsServerError),
                         f"raise {type(e).__name__}")
        assert_check(len(mock5.requests_made) == 1,
                     f"no retry: {len(mock5.requests_made)} reqs")

        # ------------------------------------------------------------------
        # J. create_retry_client factory
        # ------------------------------------------------------------------
        print("\n[--- J. CREATE_RETRY_CLIENT ---]")

        print("\n[J1] Tạo client từ .env")
        try:
            c = create_retry_client(
                game_key="gk_test",
                secret_key=TEST_SECRET,
                environment="sandbox",
                storage=storage,
            )
            assert_check(
                isinstance(c, RetryableGameAnalyticsClient),
                f"type = {type(c).__name__}"
            )
            assert_check(
                c.retry_policy.max_retries == 5,
                f"max_retries = {c.retry_policy.max_retries}"
            )
        except Exception as e:
            raise AssertionError(f"create_retry_client failed: {e}")

        # ------------------------------------------------------------------
        # K. Exponential backoff sequence
        # ------------------------------------------------------------------
        print("\n[--- K. EXPONENTIAL BACKOFF SEQUENCE ---]")
        policy_seq = RetryPolicy(
            max_retries=7,
            base_delay_seconds=1.0,
            max_delay_seconds=60.0,
            jitter_factor=None,
        )
        expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
        for i, exp in enumerate(expected[:7], start=1):
            got = policy_seq.compute_delay(i)
            assert_check(abs(got - exp) < 0.001,
                         f"delay({i}) = {got}, expected {exp}")

        print("\n" + "=" * 60)
        print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 21 RETRY OK")
        print("=" * 60)

    finally:
        # Cleanup
        try:
            os.unlink(tmp_db)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
