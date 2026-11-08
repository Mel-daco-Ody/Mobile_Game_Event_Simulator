"""Smoke test cho BƯỚC 15 – Event Validator.

Kiểm tra EventValidator (3 lớp):
A. SCHEMA validation:
    1. Event hợp lệ pass.
    2. Thiếu common field → ValidationError.
    3. category KHÔNG thuộc whitelist → ValidationError.
    4. v != 2 → ValidationError.
    5. sdk_version != 'rest api v2' → ValidationError.
    6. client_ts KHÔNG phải int / <= 0 → ValidationError.
    7. session_id KHÔNG phải UUID → ValidationError.
    8. session_num < 1 → ValidationError.
    9. user_id KHÔNG đúng format → ValidationError.
    10. platform ≠ 'ios'/'android' → ValidationError.

B. BUSINESS validation:
    11. transaction_num giảm → ValidationError.
    12. session_num giảm → ValidationError.
    13. progression event thiếu attempt_num → ValidationError.
    14. business event từ 'non_payer' → ValidationError (configurable).
    15. business event từ 'whale' → pass.
    16. resource Sink làm balance âm (disallow_negative_balance=True) → fail.
    17. resource Sink không âm → pass.

C. BATCH validation:
    18. Batch cùng category → pass.
    19. Batch mixed category (strict=True) → ValidationError.
    20. Batch có 1 event lỗi schema → ValidationError + báo index.
    21. validate_batch trả ValidationResult soft-fail.

D. Soft-fail:
    22. ValidationResult soft-fail không raise.

Chạy:
    cd prj && python gameanalytics/smoke_test_validator.py
"""

from __future__ import annotations

import json
import os
import sys
import time

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
    EventValidator,
    ValidationError,
    REQUIRED_COMMON_FIELDS,
    VALID_CATEGORIES,
)
from generators import (  # noqa: E402
    generate_business_event,
    generate_design_event,
    generate_resource_event,
    generate_players,
    generate_progression_event,
)
from models.session import generate_session  # noqa: E402
from simulation.monetization import PurchaseDecision  # noqa: E402
from simulation.economy import EconomySimulator, ResourceTransaction  # noqa: E402
from simulation.progression import (  # noqa: E402
    ABANDON, COMPLETE, FAIL, START, ProgressionAction,
)
from simulation.design import DesignDecision  # noqa: E402


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------------------
# Build a known-good event for tests (bypass generator)
# ---------------------------------------------------------------------------

def make_valid_event(user_id: str | None = None,
                     category: str = "progression") -> dict:
    base_ts = int(time.time())
    user = user_id or "player_000001"
    return {
        "category": category,
        "v": 2,
        "user_id": user,
        "client_ts": base_ts,
        "sdk_version": "rest api v2",
        "os_version": "android 15",
        "manufacturer": "google",
        "device": "SM-S918B",
        "platform": "android",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "session_num": 1,
        "build": "1.0.0",
        "event_id": "test_event",
    }


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – BƯỚC 15: EVENT VALIDATOR")
    print("=" * 60)

    v = EventValidator()
    v_strict = EventValidator(disallow_negative_balance=True)

    valid_event = make_valid_event()
    players = generate_players(count=2, seed=42)
    base_ts = int(time.time())
    session = generate_session(players[0], session_start_ts=base_ts)

    # ============================================================
    # A. SCHEMA VALIDATION
    # ============================================================
    print("\n[--- A. SCHEMA VALIDATION ---]")

    # ------------------------------------------------------------------
    # 1. Event hợp lệ pass
    # ------------------------------------------------------------------
    print("\n[1] Event hợp lệ pass")
    res = v.validate_schema(valid_event)
    assert_check(res.is_valid, "valid_event pass")
    assert_check(len(res.errors) == 0,
                 f"no errors (got {len(res.errors)})")

    # Required fields count = 11
    assert_check(len(REQUIRED_COMMON_FIELDS) == 11,
                 f"REQUIRED_COMMON_FIELDS có đúng 11: "
                 f"{len(REQUIRED_COMMON_FIELDS)}")

    # Categories = 7 theo GA spec chính thức
    # (user/session_end/progression/resource/business/design/error)
    # 'ad' đã được map sang 'design' trong ad_generator.py
    assert_check(len(VALID_CATEGORIES) == 7,
                 f"VALID_CATEGORIES có đúng 7: {len(VALID_CATEGORIES)}")
    assert_check("ad" not in VALID_CATEGORIES,
                 f"'ad' KHÔNG còn trong VALID_CATEGORIES (đã map sang 'design')")

    # ------------------------------------------------------------------
    # 2. Thiếu common field → ValidationError
    # ------------------------------------------------------------------
    print("\n[2] Thiếu common field → ValidationError")
    bad = valid_event.copy()
    del bad["user_id"]
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với thiếu user_id")
    except ValidationError as e:
        assert_check(e.field == "user_id",
                     f"ValidationError field='user_id' (got '{e.field}')")

    bad = valid_event.copy()
    del bad["session_id"]
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với thiếu session_id")
    except ValidationError as e:
        assert_check("session_id" in str(e),
                     f"ValidationError liên quan session_id")

    bad = valid_event.copy()
    del bad["session_num"]
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với thiếu session_num")
    except ValidationError as e:
        assert_check(e.field == "session_num",
                     f"ValidationError field='session_num'")

    # ------------------------------------------------------------------
    # 3. category KHÔNG hợp lệ
    # ------------------------------------------------------------------
    print("\n[3] category KHÔNG hợp lệ → ValidationError")
    bad = valid_event.copy()
    bad["category"] = "invalid_category"
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với category='invalid_category'")
    except ValidationError as e:
        assert_check(e.field == "category",
                     f"ValidationError field='category'")

    # ------------------------------------------------------------------
    # 4. v != 2
    # ------------------------------------------------------------------
    print("\n[4] v != 2 → ValidationError")
    bad = valid_event.copy()
    bad["v"] = 3
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với v=3")
    except ValidationError as e:
        assert_check(e.field == "v",
                     f"ValidationError field='v'")

    # ------------------------------------------------------------------
    # 5. sdk_version KHÔNG đúng
    # ------------------------------------------------------------------
    print("\n[5] sdk_version KHÔNG đúng → ValidationError")
    bad = valid_event.copy()
    bad["sdk_version"] = "Simulator 2.0"
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với sdk_version sai")
    except ValidationError as e:
        assert_check(e.field == "sdk_version",
                     f"ValidationError field='sdk_version'")

    # ------------------------------------------------------------------
    # 6. client_ts KHÔNG phải int / <= 0
    # ------------------------------------------------------------------
    print("\n[6] client_ts KHÔNG phải int → ValidationError")
    bad = valid_event.copy()
    bad["client_ts"] = "1759305600"  # str
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với client_ts str")
    except ValidationError as e:
        assert_check(e.field == "client_ts",
                     f"ValidationError field='client_ts'")

    bad = valid_event.copy()
    bad["client_ts"] = -1
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với client_ts=-1")
    except ValidationError as e:
        assert_check(e.field == "client_ts",
                     f"ValidationError field='client_ts' (negative)")

    # ------------------------------------------------------------------
    # 7. session_id KHÔNG đúng UUID
    # ------------------------------------------------------------------
    print("\n[7] session_id KHÔNG đúng UUID → ValidationError")
    bad = valid_event.copy()
    bad["session_id"] = "not-a-uuid"
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với session_id='not-a-uuid'")
    except ValidationError as e:
        assert_check(e.field == "session_id",
                     f"ValidationError field='session_id' (UUID)")

    # ------------------------------------------------------------------
    # 8. session_num < 1
    # ------------------------------------------------------------------
    print("\n[8] session_num < 1 → ValidationError")
    bad = valid_event.copy()
    bad["session_num"] = 0
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với session_num=0")
    except ValidationError as e:
        assert_check(e.field == "session_num",
                     f"ValidationError field='session_num' (0)")

    # ------------------------------------------------------------------
    # 9. user_id KHÔNG đúng format
    # ------------------------------------------------------------------
    print("\n[9] user_id KHÔNG đúng format → ValidationError")
    bad = valid_event.copy()
    bad["user_id"] = "p_001"  # sai format
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với user_id='p_001'")
    except ValidationError as e:
        assert_check(e.field == "user_id",
                     f"ValidationError field='user_id' (format)")

    # ------------------------------------------------------------------
    # 10. platform KHÔNG hợp lệ
    # ------------------------------------------------------------------
    print("\n[10] platform KHÔNG hợp lệ → ValidationError")
    bad = valid_event.copy()
    bad["platform"] = "windows"
    try:
        v.validate_schema(bad)
        raise AssertionError("Không raise với platform='windows'")
    except ValidationError as e:
        assert_check(e.field == "platform",
                     f"ValidationError field='platform'")

    # Soft-fail: validate_schema trả result thay vì raise
    print("\n[10b] Soft-fail: validate_schema với result=")
    bad = make_valid_event()
    bad["platform"] = "windows"
    res = v.validate_schema(bad, result=__import__(
        "gameanalytics.validator", fromlist=["ValidationResult"]
    ).ValidationResult(is_valid=True))
    assert_check(not res.is_valid,
                 "soft-fail result.is_valid=False")
    assert_check(any(e.field == "platform" for e in res.errors),
                 "soft-fail có error field='platform'")

    # ============================================================
    # B. BUSINESS VALIDATION
    # ============================================================
    print("\n[--- B. BUSINESS VALIDATION ---]")

    # ------------------------------------------------------------------
    # 11. transaction_num giảm
    # ------------------------------------------------------------------
    print("\n[11] transaction_num giảm → ValidationError")
    bad = make_valid_event(category="resource")
    bad["transaction_num"] = 5
    prev = make_valid_event(category="resource")
    prev["transaction_num"] = 10
    try:
        v.validate_business(bad, previous_event=prev)
        raise AssertionError("Không raise với trn 10→5")
    except ValidationError as e:
        assert_check(e.field == "transaction_num",
                     f"ValidationError field='transaction_num'")

    # tăng → pass
    bad_inc = make_valid_event(category="resource")
    bad_inc["transaction_num"] = 11
    res = v.validate_business(bad_inc, previous_event=prev)
    assert_check(res.is_valid, "transaction_num 10→11 ok")

    # ------------------------------------------------------------------
    # 12. session_num giảm
    # ------------------------------------------------------------------
    print("\n[12] session_num giảm → ValidationError")
    prev_b = make_valid_event()
    prev_b["session_num"] = 5
    cur_b = make_valid_event()
    cur_b["session_num"] = 3
    try:
        v.validate_business(cur_b, previous_event=prev_b)
        raise AssertionError("Không raise với session_num 5→3")
    except ValidationError as e:
        assert_check(e.field == "session_num",
                     f"ValidationError field='session_num' (monotonic)")

    # ------------------------------------------------------------------
    # 13. progression thiếu attempt_num
    # ------------------------------------------------------------------
    print("\n[13] progression thiếu attempt_num → ValidationError")
    bad = make_valid_event(category="progression")
    # không có attempt_num
    try:
        v.validate_business(bad)
        raise AssertionError("Không raise với progression thiếu attempt_num")
    except ValidationError as e:
        assert_check(e.field == "attempt_num",
                     f"ValidationError field='attempt_num'")

    bad_a = make_valid_event(category="progression")
    bad_a["attempt_num"] = 2
    res = v.validate_business(bad_a)
    assert_check(res.is_valid, "progression attempt_num=2 pass")

    # ------------------------------------------------------------------
    # 14. business event từ 'non_payer'
    # ------------------------------------------------------------------
    print("\n[14] purchase từ 'non_payer' → ValidationError")
    bad = make_valid_event(category="business")
    player_state_non = {"payer_segment": "non_payer"}
    try:
        v.validate_business(bad, player_state=player_state_non)
        raise AssertionError("Không raise với non_payer")
    except ValidationError as e:
        assert_check(e.field == "payer_segment",
                     f"ValidationError field='payer_segment' (non_payer)")

    # ------------------------------------------------------------------
    # 15. business event từ 'whale'
    # ------------------------------------------------------------------
    print("\n[15] purchase từ 'whale' → pass")
    player_state_whale = {"payer_segment": "whale"}
    res = v.validate_business(bad, player_state=player_state_whale)
    assert_check(res.is_valid, "whale purchase pass")

    # ------------------------------------------------------------------
    # 16. resource Sink làm balance âm (disallow_negative_balance)
    # ------------------------------------------------------------------
    print("\n[16] resource Sink âm (disallow=True) → fail")
    bad_res = make_valid_event(category="resource")
    bad_res["flow_type"] = "Sink"
    bad_res["virtual_currency"] = "Coins"
    bad_res["amount"] = -10000
    player_state = {"coins": 50}
    from gameanalytics.validator import ValidationResult
    res = v_strict.validate_business(
        bad_res, player_state=player_state,
        result=ValidationResult(is_valid=True),
    )
    assert_check(not res.is_valid,
                 "Sink âm balance → fail (disallow=True)")
    assert_check(any(e.field == "amount" for e in res.errors),
                 "error field='amount'")

    # ------------------------------------------------------------------
    # 17. resource Sink không âm → pass
    # ------------------------------------------------------------------
    print("\n[17] resource Sink OK (disallow=True) → pass")
    ok_res = make_valid_event(category="resource")
    ok_res["flow_type"] = "Sink"
    ok_res["virtual_currency"] = "Coins"
    ok_res["amount"] = -100
    player_state_enough = {"coins": 1000}
    res = v_strict.validate_business(ok_res, player_state=player_state_enough)
    assert_check(res.is_valid,
                 "Sink -100 từ balance=1000 pass")

    # ============================================================
    # C. BATCH VALIDATION
    # ============================================================
    print("\n[--- C. BATCH VALIDATION ---]")

    # ------------------------------------------------------------------
    # 18. Batch cùng category → pass
    # ------------------------------------------------------------------
    print("\n[18] Batch cùng category → pass")
    design_batch = []
    for i in range(20):
        ev = make_valid_event(category="design")
        ev["event_id"] = f"Gameplay:Kill:Zombie_{i}"
        design_batch.append(ev)
    res = v.validate_batch(design_batch, strict_category=True)
    assert_check(res.is_valid, "20 design events batch pass")

    # ------------------------------------------------------------------
    # 19. Batch mixed category
    # ------------------------------------------------------------------
    print("\n[19] Batch mixed category (strict=True) → fail")
    mixed = design_batch.copy()
    extra = make_valid_event(category="business")
    mixed.append(extra)
    res = v.validate_batch(mixed, strict_category=True)
    assert_check(not res.is_valid,
                 "Mixed category fail")
    assert_check(any(e.field == "<batch>" for e in res.errors),
                 "error field='<batch>'")

    # strict=False → allow
    res_lax = v.validate_batch(mixed, strict_category=False)
    assert_check(res_lax.is_valid or len(res_lax.errors) == 0,
                 "Mixed category strict=False → OK")

    # ------------------------------------------------------------------
    # 20. Batch có 1 event lỗi schema → báo index
    # ------------------------------------------------------------------
    print("\n[20] Batch có event lỗi schema → ValidationError + báo index")
    batch_one_bad = [make_valid_event(category="design") for _ in range(10)]
    # Inject lỗi tại event#5
    batch_one_bad[5]["client_ts"] = "not_int"
    res = v.validate_batch(batch_one_bad, strict_category=True)
    assert_check(not res.is_valid, "batch có lỗi")
    assert_check(any("[event#5]" in str(e) for e in res.errors),
                 f"[event#5] trong error message")

    # ------------------------------------------------------------------
    # 21. Batch validation mọi event cùng category
    # ------------------------------------------------------------------
    print("\n[21] Batch validation strict category")
    # Mixed nhưng mỗi event schema OK
    only_design = [make_valid_event(category="design")
                   for _ in range(5)]
    res = v.validate_batch(only_design, strict_category=False)
    assert_check(res.is_valid, "only_design batch OK")

    # ============================================================
    # D. SOFT-FAIL
    # ============================================================
    print("\n[--- D. SOFT-FAIL MODE ---]")

    # ------------------------------------------------------------------
    # 22. ValidationResult soft-fail không raise
    # ------------------------------------------------------------------
    print("\n[22] ValidationResult soft-fail")
    from gameanalytics.validator import ValidationResult
    bad = make_valid_event()
    bad["v"] = 5  # sai
    bad["sdk_version"] = "python 1.0"  # sai
    bad["client_ts"] = "not_int"  # sai
    res = v.validate_schema(bad, result=ValidationResult(is_valid=True))
    assert_check(not res.is_valid, "soft-fail is_valid=False")
    assert_check(len(res.errors) == 3,
                 f"3 errors (got {len(res.errors)})")
    assert_check(bool(res) is False, "bool(res)==False")
    # raise_if_invalid
    try:
        res.raise_if_invalid()
        raise AssertionError("raise_if_invalid không raise")
    except ValidationError as e:
        assert_check(True, f"raise_if_invalid: {e}")

    # ============================================================
    # E. END-TO-END với event thật từ generators
    # ============================================================
    print("\n[--- E. END-TO-END với generator thật ---]")

    # Tạo event bằng generator thật rồi validate
    real_progression = generate_progression_event(
        players[0], session,
        ProgressionAction(
            client_ts=base_ts,
            event_id="Start:World1:Level05",
            start_system=1,
            end_system=5,
            complete_status=START,
            attempt_num=1,
        ),
    )
    res = v.validate_schema(real_progression)
    assert_check(res.is_valid, "real progression event pass schema")

    real_design = generate_design_event(
        players[0], session,
        DesignDecision(
            client_ts=base_ts + 10,
            event_id="Gameplay:Kill:Boss",
            value=738,
            custom_fields={},
        ),
    )
    res = v.validate_schema(real_design)
    assert_check(res.is_valid, "real design event pass schema")

    real_resource = generate_resource_event(
        players[0], session,
        ResourceTransaction(
            client_ts=base_ts + 20,
            flow_type="Source",
            virtual_currency="Coins",
            item_type="Gameplay",
            item_id="LevelReward",
            amount=100,
        ),
    )
    res = v.validate_schema(real_resource)
    assert_check(res.is_valid, "real resource event pass schema")

    # Purchase từ whale
    whale_player = players[1]
    if hasattr(whale_player, "payer_segment"):
        whale_player.payer_segment = "whale"
    eco = EconomySimulator(rng=None)
    purchase = PurchaseDecision(
        client_ts=base_ts + 30,
        product_id="gem_pack_small",
        product_type="GemPack",
        amount=99,           # minor units (e.g., cents)
        currency="USD",
        transaction_num=1,
        cart_type="menu_shop",
        gem_amount=100,
    )
    real_business = generate_business_event(whale_player, session, purchase)
    res = v.validate_schema(real_business)
    assert_check(res.is_valid, "real business event pass schema")

    # Business chain: transaction_num giảm
    chain_ev1 = real_business.copy()
    chain_ev1["transaction_num"] = 1
    chain_ev2 = real_business.copy()
    chain_ev2["transaction_num"] = 2
    res = v.validate_business(chain_ev2, previous_event=chain_ev1)
    assert_check(res.is_valid, "transaction_num 1→2 pass")

    chain_ev3 = real_business.copy()
    chain_ev3["transaction_num"] = 1  # giảm
    res = v.validate_business(
        chain_ev3, previous_event=chain_ev2,
        result=ValidationResult(is_valid=True),
    )
    assert_check(not res.is_valid, "transaction_num 2→1 fail")

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – BƯỚC 15 VALIDATOR OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
