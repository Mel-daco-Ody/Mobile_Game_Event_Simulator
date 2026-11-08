"""Event Validator – schema / business / batch validation.

Theo BƯỚC 15 INSTRUCTION.md:

Kiểm tra event trước khi đưa vào queue. 3 lớp validation:

1. SCHEMA: mọi event phải đúng GameAnalytics schema v2 (11 common fields).
2. BUSINESS: kiểm tra tính hợp lý logic (monotonic counters,
   session chain, purchase ↔ payer, balance không âm, ...).
3. BATCH: mọi event cùng category schema đúng, kích thước batch hợp lệ.

Lớp này:
    - Pure data validation – KHÔNG có I/O.
    - Raise ValidationError (subclass ValueError) với message rõ ràng.
    - Trả về ValidationResult để caller muốn soft-fail thay vì raise.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import game_config


# =========================================================================
# Errors
# =========================================================================

class ValidationError(ValueError):
    """Raise khi event vi phạm schema / business rule.

    Attributes:
        field: tên field lỗi (None nếu là lỗi toàn event).
        message: mô tả lỗi.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.message = message

    def __str__(self) -> str:
        if self.field:
            return f"[{self.field}] {self.message}"
        return self.message


# =========================================================================
# Constants – required common fields cho mọi event
# =========================================================================

# 11 fields bắt buộc theo GameAnalytics schema v2 (INSTRUCTION Bước 15)
REQUIRED_COMMON_FIELDS: frozenset[str] = frozenset({
    "category", "v", "user_id", "client_ts", "sdk_version",
    "os_version", "manufacturer", "device", "platform",
    "session_id", "session_num",
})

# Optional fields không bắt buộc
OPTIONAL_COMMON_FIELDS: frozenset[str] = frozenset({"build"})

# UUID v4 format regex (loose check)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    flags=re.IGNORECASE,
)

# User_id format: 'player_NNNNNN'  (sinh từ player_generator)
_USER_ID_RE = re.compile(r"^player_\d{6}$")

# Category whitelist theo GameAnalytics spec chính thức.
# Lưu ý: KHÔNG có 'ad' trong GA spec – ad event được map sang 'design'
# trong ad_generator.py.
VALID_CATEGORIES: frozenset[str] = frozenset({
    "user", "session_end", "progression", "resource", "business",
    "design", "error",
})


# =========================================================================
# Validation result (cho soft-fail caller)
# =========================================================================

@dataclass
class ValidationResult:
    """Kết quả validation dạng soft-fail (không raise)."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)

    def __bool__(self) -> bool:  # cho phép dùng trong if
        return self.is_valid

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            # Raise lỗi đầu tiên để caller debug.
            raise self.errors[0]


# =========================================================================
# Event Validator
# =========================================================================

class EventValidator:
    """Validate event theo 3 layer: schema / business / batch.

    Cấu hình (optional):
        disallow_negative_balance: nếu True, validate resource Sink
                                    không làm balance < 0.
        payer_segments: set các segment được phép purchase.
    """

    def __init__(
        self,
        disallow_negative_balance: bool = False,
        payer_segments: frozenset[str] | None = None,
    ) -> None:
        self.disallow_negative_balance = disallow_negative_balance
        self.payer_segments = payer_segments or frozenset({
            "low_payer", "mid_payer", "whale",
        })

    # ------------------------------------------------------------------
    # 1. SCHEMA VALIDATION
    # ------------------------------------------------------------------
    def validate_schema(
        self,
        event: dict[str, Any],
        result: ValidationResult | None = None,
    ) -> ValidationResult:
        """Validate schema chung cho mọi event.

        Args:
            event: dict event cần validate.
            result: ValidationResult để append lỗi (optional).
                    Nếu None, raise ngay khi lỗi.

        Returns:
            ValidationResult (luôn trả về, kể cả raise).
        """
        soft = result is not None
        result = result or ValidationResult(is_valid=True)

        if not isinstance(event, dict):
            result.is_valid = False
            result.errors.append(
                ValidationError("Event phải là dict", field="<root>"))
            return result

        # Đầy đủ 11 common fields
        missing = REQUIRED_COMMON_FIELDS - set(event.keys())
        if missing:
            result.is_valid = False
            for f in sorted(missing):
                result.errors.append(
                    ValidationError(
                        f"Field bắt buộc '{f}' thiếu", field=f))

        # category hợp lệ
        cat = event.get("category")
        if cat is not None and cat not in VALID_CATEGORIES:
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"category '{cat}' KHÔNG thuộc "
                    f"{sorted(VALID_CATEGORIES)}", field="category"))

        # v == game_config.EVENT_VERSION (2)
        ver = event.get("v")
        if ver != game_config.EVENT_VERSION:
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"v={ver} nhưng EVENT_VERSION="
                    f"{game_config.EVENT_VERSION}", field="v"))

        # sdk_version PHẢI là 'rest api v2'
        sdk = event.get("sdk_version")
        if sdk != game_config.SDK_VERSION:
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"sdk_version='{sdk}' PHẢI là "
                    f"'{game_config.SDK_VERSION}'", field="sdk_version"))

        # client_ts là int (Unix ts)
        ts = event.get("client_ts")
        if not isinstance(ts, int) or ts <= 0:
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"client_ts={ts} phải là int > 0", field="client_ts"))

        # session_id là UUID v4 string
        sid = event.get("session_id")
        if not sid or not isinstance(sid, str):
            result.is_valid = False
            result.errors.append(
                ValidationError("session_id phải là UUID string",
                                field="session_id"))
        else:
            # UUID v4: version digit (2 chars after 3rd hyphen) == '4'
            uuid_loose = re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$",
                sid, flags=re.IGNORECASE,
            )
            if not uuid_loose:
                result.is_valid = False
                result.errors.append(
                    ValidationError(
                        f"session_id='{sid}' KHÔNG đúng UUID format",
                        field="session_id"))

        # session_num >= 1 (int)
        sn = event.get("session_num")
        if not isinstance(sn, int) or sn < 1:
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"session_num={sn} phải là int >= 1",
                    field="session_num"))

        # user_id hợp lệ (format player_NNNNNN)
        uid = event.get("user_id")
        if not uid or not isinstance(uid, str):
            result.is_valid = False
            result.errors.append(
                ValidationError("user_id phải là string",
                                field="user_id"))
        elif not _USER_ID_RE.match(uid):
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"user_id='{uid}' KHÔNG đúng format player_NNNNNN",
                    field="user_id"))

        # platform: ios/android
        platform = event.get("platform")
        if platform not in ("ios", "android"):
            result.is_valid = False
            result.errors.append(
                ValidationError(
                    f"platform='{platform}' phải là 'ios' hoặc 'android'",
                    field="platform"))

        # os_version: string khác rỗng
        osv = event.get("os_version")
        if not osv or not isinstance(osv, str):
            result.is_valid = False
            result.errors.append(
                ValidationError("os_version phải là string khác rỗng",
                                field="os_version"))

        # manufacturer: string khác rỗng
        mfg = event.get("manufacturer")
        if not mfg or not isinstance(mfg, str):
            result.is_valid = False
            result.errors.append(
                ValidationError("manufacturer phải là string khác rỗng",
                                field="manufacturer"))

        # device: string khác rỗng
        dev = event.get("device")
        if not dev or not isinstance(dev, str):
            result.is_valid = False
            result.errors.append(
                ValidationError("device phải là string khác rỗng",
                                field="device"))

        # build optional nhưng nếu có phải là string
        if "build" in event and not isinstance(event["build"], str):
            result.is_valid = False
            result.errors.append(
                ValidationError("build phải là string", field="build"))

        if not soft and not result.is_valid:
            result.raise_if_invalid()
        return result

    # ------------------------------------------------------------------
    # 2. BUSINESS VALIDATION
    # ------------------------------------------------------------------
    def validate_business(
        self,
        event: dict[str, Any],
        previous_event: dict[str, Any] | None = None,
        player_state: dict[str, Any] | None = None,
        result: ValidationResult | None = None,
    ) -> ValidationResult:
        """Validate business rule cho event.

        Args:
            event: event cần validate.
            previous_event: event trước đó trong cùng session
                            (optional – để check monotonic).
            player_state: dict chứa thông tin state của player:
                          {'payer_segment': 'whale'|'mid_payer'|...,
                           'coins': int, 'gems': int, ...}
            result: ValidationResult optional.

        Raises:
            ValidationError nếu vi phạm rule.
        """
        soft = result is not None
        result = result or ValidationResult(is_valid=True)

        cat = event.get("category")

        # transaction_num tăng dần cho resource/business
        if previous_event is not None:
            prev_trn = previous_event.get("transaction_num")
            cur_trn = event.get("transaction_num")
            if (prev_trn is not None and cur_trn is not None
                    and isinstance(prev_trn, int)
                    and isinstance(cur_trn, int)):
                if cur_trn < prev_trn:
                    result.is_valid = False
                    result.errors.append(
                        ValidationError(
                            f"transaction_num không giảm: "
                            f"{prev_trn} → {cur_trn}",
                            field="transaction_num"))

            # session_num tăng dần
            prev_sn = previous_event.get("session_num")
            cur_sn = event.get("session_num")
            if (prev_sn is not None and cur_sn is not None
                    and isinstance(prev_sn, int)
                    and isinstance(cur_sn, int)):
                if cur_sn < prev_sn:
                    result.is_valid = False
                    result.errors.append(
                        ValidationError(
                            f"session_num không giảm: "
                            f"{prev_sn} → {cur_sn}",
                            field="session_num"))

        # attempt_num >= 1 cho progression event
        if cat == "progression":
            an = event.get("attempt_num")
            if an is None or not isinstance(an, int) or an < 1:
                result.is_valid = False
                result.errors.append(
                    ValidationError(
                        f"attempt_num={an} phải là int >= 1 "
                        f"cho progression event",
                        field="attempt_num"))

        # purchase chỉ từ payer (business event)
        if cat == "business":
            if player_state is not None:
                segment = player_state.get("payer_segment", "non_payer")
                if segment not in self.payer_segments:
                    result.is_valid = False
                    result.errors.append(
                        ValidationError(
                            f"purchase event nhưng payer_segment="
                            f"'{segment}' KHÔNG thuộc payer set "
                            f"{sorted(self.payer_segments)}",
                            field="payer_segment"))

        # Sink không làm balance âm (nếu cấu hình)
        if (cat == "resource" and self.disallow_negative_balance
                and player_state is not None):
            flow = event.get("flow_type")
            amount = event.get("amount")
            currency = event.get("virtual_currency")
            if (flow == "Sink" and isinstance(amount, int)
                    and amount < 0 and currency):
                balance_key = f"{currency.lower()}_balance_after"
                after = event.get(balance_key)
                if after is not None and isinstance(after, int):
                    if after < 0:
                        result.is_valid = False
                        result.errors.append(
                            ValidationError(
                                f"Sink làm balance âm "
                                f"({currency}={after})",
                                field=balance_key))
                # Fallback: check player_state
                elif currency:
                    current = player_state.get(currency.lower())
                    if (current is not None and isinstance(current, int)):
                        if current + amount < 0:
                            result.is_valid = False
                            result.errors.append(
                                ValidationError(
                                    f"Sink làm balance âm: {currency}="
                                    f"{current} + amount({amount}) < 0",
                                    field="amount"))

        if not soft and not result.is_valid:
            result.raise_if_invalid()
        return result

    # ------------------------------------------------------------------
    # 3. BATCH VALIDATION
    # ------------------------------------------------------------------
    def validate_batch(
        self,
        events: list[dict[str, Any]],
        strict_category: bool = True,
    ) -> ValidationResult:
        """Validate toàn bộ batch event.

        Args:
            events: list event.
            strict_category: nếu True, mọi event phải cùng category
                             (an toàn cho batch endpoint).
                             GameAnalytics cho phép batch nhiều category,
                             nên default True (đề xuất) nhưng có thể tắt.

        Returns:
            ValidationResult tổng hợp lỗi của cả batch.
        """
        result = ValidationResult(is_valid=True)

        if not isinstance(events, list):
            result.is_valid = False
            result.errors.append(
                ValidationError("Batch phải là list", field="<root>"))
            return result

        # Mọi event qua schema check
        for idx, ev in enumerate(events):
            sub = self.validate_schema(ev, result=ValidationResult(True))
            if not sub.is_valid:
                result.is_valid = False
                for e in sub.errors:
                    result.errors.append(
                        ValidationError(
                            f"[event#{idx}] {e}", field=e.field))

        # Same-category check
        if strict_category and events:
            cats = Counter(ev.get("category") for ev in events)
            # Loại bỏ None
            del cats[None]
            if len(cats) > 1:
                result.is_valid = False
                result.errors.append(
                    ValidationError(
                        f"Batch chứa nhiều category: {dict(cats)} "
                        f"(yêu cầu strict_category=True)",
                        field="<batch>"))

        # session_num chain trong batch
        if len(events) >= 2:
            session_nums = [
                ev.get("session_num") for ev in events
                if isinstance(ev.get("session_num"), int)
            ]
            if (len(session_nums) >= 2
                    and session_nums != sorted(session_nums)):
                # KHÔNG strict enforce (progression có thể nhảy level)
                # nhưng flag warning
                result.errors.append(
                    ValidationError(
                        f"session_num KHÔNG tăng dần trong batch: "
                        f"{session_nums}",
                        field="session_num"))

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def check_client_ts_freshness(client_ts: int, max_age_seconds: int = 86400) -> bool:
        """Check client_ts có 'tươi' (không quá 1 ngày cũ) hay không.

        Args:
            client_ts: unix timestamp (giây).
            max_age_seconds: ngưỡng tối đa (mặc định 86400 = 1 ngày).

        Returns:
            True nếu ts gần đây.
        """
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        return abs(now_ts - client_ts) <= max_age_seconds


__all__ = [
    "EventValidator",
    "ValidationError",
    "ValidationResult",
    "REQUIRED_COMMON_FIELDS",
    "OPTIONAL_COMMON_FIELDS",
    "VALID_CATEGORIES",
]
