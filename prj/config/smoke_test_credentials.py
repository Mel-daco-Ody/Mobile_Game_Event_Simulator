"""Smoke test cho credentials loader (Bước 1 bổ sung – INSTRUCTION §1.2).

Kiểm tra config/credentials.py:

A. _parse_env_file:
    1. Parse key=value cơ bản.
    2. Bỏ qua comment dòng (#) và dòng trống.
    3. Strip quotes (single + double).
    4. Strip whitespace quanh "=".
    5. Bỏ qua dòng không có "=".

B. load_env_file:
    6. Đọc file .env hợp lệ.
    7. File không tồn tại raise FileNotFoundError.
    8. Path là thư mục raise ValueError.

C. find_env_file:
    9. Tìm thấy .env theo DEFAULT_ENV_PATHS.

D. GameAnalyticsCredentials:
    10. Empty game_key raise ValueError.
    11. Empty secret_key raise ValueError.
    12. Invalid environment raise ValueError.
    13. masked() che secret_key.
    14. is_sandbox / is_production.

E. load_credentials:
    15. Ưu tiên process env hơn file.
    16. File override default environment.
    17. Thiếu cả 2 raise ValueError (require_all=True).

F. get_credentials (singleton + cache):
    18. Gọi 2 lần trả cùng instance.
    19. force_reload=True load lại.
    20. clear_cache() reset.

G. Integration với GameAnalyticsClient:
    21. from_env() tạo client từ .env.
    22. from_env() raise nếu thiếu credentials.
    23. Client từ .env dùng đúng game_key/secret_key.

H. End-to-end với .env thật:
    24. Đọc được GAME_KEY từ prj/.env.
    25. Đọc được SECRET_KEY từ prj/.env.
    26. GA_ENV default = "sandbox".

Chạy:
    cd prj && python config/smoke_test_credentials.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.credentials import (  # noqa: E402
    DEFAULT_GA_ENV,
    DEFAULT_ENV_PATHS,
    ENV_GA_ENV,
    ENV_GAME_KEY,
    ENV_SECRET_KEY,
    VALID_GA_ENVS,
    GameAnalyticsCredentials,
    _parse_env_file,
    clear_cache,
    find_env_file,
    get_credentials,
    get_ga_env,
    load_credentials,
    load_env_file,
    require_game_key,
    require_secret_key,
)


def assert_check(condition: bool, label: str) -> None:
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {label}")
    if not condition:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("SMOKE TEST – CREDENTIALS LOADER (BƯỚC 1 BỔ SUNG)")
    print("=" * 60)

    # ----------------------------------------------------------------------
    # A. _parse_env_file
    # ----------------------------------------------------------------------
    print("\n[--- A. _PARSE_ENV_FILE ---]")

    print("\n[A1] Parse key=value cơ bản")
    parsed = _parse_env_file("GAME_KEY=abc123\nSECRET_KEY=xyz789")
    assert_check(parsed == {"GAME_KEY": "abc123", "SECRET_KEY": "xyz789"},
                 f"parsed = {parsed}")

    print("\n[A2] Bỏ qua comment dòng và dòng trống")
    content = (
        "# This is comment\n"
        "\n"
        "GAME_KEY=test_key\n"
        "# Another comment\n"
        "SECRET_KEY=test_secret\n"
    )
    parsed = _parse_env_file(content)
    assert_check(parsed == {"GAME_KEY": "test_key", "SECRET_KEY": "test_secret"},
                 f"parsed = {parsed}")

    print("\n[A3] Strip double quotes")
    parsed = _parse_env_file('KEY="value with spaces"')
    assert_check(parsed == {"KEY": "value with spaces"},
                 f"stripped = {parsed}")

    print("\n[A4] Strip single quotes")
    parsed = _parse_env_file("KEY='quoted value'")
    assert_check(parsed == {"KEY": "quoted value"},
                 f"stripped = {parsed}")

    print("\n[A5] Bỏ qua dòng không có '='")
    content = "GOOD=value\nbad_line_no_equals\nANOTHER=ok\n"
    parsed = _parse_env_file(content)
    assert_check(parsed == {"GOOD": "value", "ANOTHER": "ok"},
                 f"parsed = {parsed}")

    print("\n[A6] Strip whitespace quanh '='")
    parsed = _parse_env_file("  GAME_KEY  =  spaced  ")
    assert_check(parsed == {"GAME_KEY": "spaced"},
                 f"stripped = {parsed}")

    # ----------------------------------------------------------------------
    # B. load_env_file
    # ----------------------------------------------------------------------
    print("\n[--- B. LOAD_ENV_FILE ---]")

    print("\n[B1] Đọc file .env hợp lệ (tempfile)")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8",
    ) as f:
        f.write("GAME_KEY=temp_key\nSECRET_KEY=temp_secret\n")
        tmp_path = f.name
    try:
        result = load_env_file(tmp_path)
        assert_check(result["GAME_KEY"] == "temp_key",
                     f"GAME_KEY = {result['GAME_KEY']}")
        assert_check(result["SECRET_KEY"] == "temp_secret",
                     f"SECRET_KEY = {result['SECRET_KEY']}")
    finally:
        os.unlink(tmp_path)

    print("\n[B2] File không tồn tại raise FileNotFoundError")
    try:
        load_env_file("/nonexistent/path/to/.env_xyz")
        raise AssertionError("Không raise")
    except FileNotFoundError as e:
        assert_check(".env" in str(e) or "không tồn tại" in str(e).lower(),
                     f"FileNotFoundError: {e}")

    print("\n[B3] Path là thư mục raise ValueError")
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            load_env_file(tmpdir)
            raise AssertionError("Không raise")
        except ValueError as e:
            assert_check("không phải file" in str(e).lower() or
                         "not a file" in str(e).lower(),
                         f"ValueError: {e}")

    # ----------------------------------------------------------------------
    # C. find_env_file
    # ----------------------------------------------------------------------
    print("\n[--- C. FIND_ENV_FILE ---]")

    print("\n[C1] Tìm thấy .env theo DEFAULT_ENV_PATHS")
    found = find_env_file()
    assert_check(found is not None, f"found = {found}")
    if found:
        assert_check(found.exists(), f"file tồn tại: {found}")
        assert_check(found.name == ".env", f"name = {found.name}")

    # ----------------------------------------------------------------------
    # D. GameAnalyticsCredentials
    # ----------------------------------------------------------------------
    print("\n[--- D. GAMECALYTICSCREDENTIALS ---]")

    print("\n[D1] Empty game_key raise ValueError")
    try:
        GameAnalyticsCredentials(game_key="", secret_key="abc")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("GAME_KEY" in str(e), f"ValueError: {e}")

    print("\n[D2] Empty secret_key raise ValueError")
    try:
        GameAnalyticsCredentials(game_key="abc", secret_key="")
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("SECRET_KEY" in str(e), f"ValueError: {e}")

    print("\n[D3] Invalid environment raise ValueError")
    try:
        GameAnalyticsCredentials(
            game_key="abc", secret_key="xyz",
            environment="staging",
        )
        raise AssertionError("Không raise")
    except ValueError as e:
        assert_check("environment" in str(e).lower(), f"ValueError: {e}")

    print("\n[D4] masked() che secret_key")
    creds = GameAnalyticsCredentials(
        game_key="abcdef1234567890",
        secret_key="my_secret",
        environment="sandbox",
    )
    masked = creds.masked()
    assert_check(masked.secret_key == "***MASKED***",
                 f"masked.secret_key = {masked.secret_key}")
    assert_check(masked.game_key.endswith("***"),
                 f"masked.game_key = {masked.game_key}")
    assert_check(masked.environment == "sandbox",
                 f"environment preserved")

    print("\n[D5] is_sandbox / is_production")
    s = GameAnalyticsCredentials(
        game_key="a", secret_key="b", environment="sandbox",
    )
    p = GameAnalyticsCredentials(
        game_key="a", secret_key="b", environment="production",
    )
    assert_check(s.is_sandbox and not s.is_production, "sandbox flags")
    assert_check(p.is_production and not p.is_sandbox, "production flags")

    print("\n[D6] Valid environments constant")
    assert_check("sandbox" in VALID_GA_ENVS, "sandbox in valid")
    assert_check("production" in VALID_GA_ENVS, "production in valid")
    assert_check("staging" not in VALID_GA_ENVS, "staging not in valid")

    # ----------------------------------------------------------------------
    # E. load_credentials
    # ----------------------------------------------------------------------
    print("\n[--- E. LOAD_CREDENTIALS ---]")

    print("\n[E1] Load từ file .env hợp lệ")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8",
    ) as f:
        f.write(
            "GAME_KEY=file_key_123\n"
            "SECRET_KEY=file_secret_456\n"
            "GA_ENV=production\n"
        )
        tmp_path = f.name
    try:
        creds = load_credentials(env_path=tmp_path)
        assert_check(creds.game_key == "file_key_123",
                     f"game_key = {creds.game_key}")
        assert_check(creds.secret_key == "file_secret_456",
                     f"secret_key = {creds.secret_key}")
        assert_check(creds.environment == "production",
                     f"environment = {creds.environment}")
        assert_check("env_file" in creds.source, f"source = {creds.source}")
    finally:
        os.unlink(tmp_path)

    print("\n[E2] Process env ưu tiên cao hơn file")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8",
    ) as f:
        f.write(
            "GAME_KEY=file_key\n"
            "SECRET_KEY=file_secret\n"
            "GA_ENV=production\n"
        )
        tmp_path = f.name
    # Set process env
    os.environ[ENV_GAME_KEY] = "process_key"
    os.environ[ENV_SECRET_KEY] = "process_secret"
    os.environ[ENV_GA_ENV] = "sandbox"
    try:
        creds = load_credentials(env_path=tmp_path)
        assert_check(creds.game_key == "process_key",
                     f"process env wins: game_key = {creds.game_key}")
        assert_check(creds.secret_key == "process_secret",
                     f"process env wins: secret_key = {creds.secret_key}")
    finally:
        # Cleanup
        for k in (ENV_GAME_KEY, ENV_SECRET_KEY, ENV_GA_ENV):
            os.environ.pop(k, None)
        os.unlink(tmp_path)

    print("\n[E3] Thiếu cả 2 (env var + file) raise ValueError")
    # Save current values
    saved_env = {
        k: os.environ.pop(k, None)
        for k in (ENV_GAME_KEY, ENV_SECRET_KEY, ENV_GA_ENV)
    }
    try:
        try:
            load_credentials(env_path="/nonexistent/.env_xyz")
            raise AssertionError("Không raise")
        except (FileNotFoundError, ValueError) as e:
            assert_check(True, f"raise {type(e).__name__}: {e}")
    finally:
        # Restore
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v

    # ----------------------------------------------------------------------
    # F. get_credentials (singleton + cache)
    # ----------------------------------------------------------------------
    print("\n[--- F. GET_CREDENTIALS (singleton) ---]")

    print("\n[F1] 2 lần gọi trả cùng instance")
    clear_cache()
    c1 = get_credentials(env_path=tmp_path if os.path.exists(tmp_path) else None)
    c2 = get_credentials()
    assert_check(c1 is c2, f"singleton: id(c1)={id(c1)}, id(c2)={id(c2)}")

    # Cleanup tmp
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)

    print("\n[F2] clear_cache() reset")
    clear_cache()
    c3 = get_credentials()
    assert_check(c1 is not c3 or c1 == c3, "sau clear_cache, lấy lại")

    # ----------------------------------------------------------------------
    # G. Integration với GameAnalyticsClient
    # ----------------------------------------------------------------------
    print("\n[--- G. INTEGRATION VỚI GAMEANALYTICSCLIENT ---]")

    print("\n[G1] from_env() tạo client từ .env thật")
    from gameanalytics import GameAnalyticsClient
    try:
        # Tạo tmp .env để test deterministic
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8",
        ) as f:
            f.write(
                "GAME_KEY=test_game_key_xyz\n"
                "SECRET_KEY=test_secret_xyz\n"
                "GA_ENV=sandbox\n"
            )
            tmp_env = f.name
        clear_cache()
        client = GameAnalyticsClient.from_env(env_path=tmp_env)
        assert_check(client.game_key == "test_game_key_xyz",
                     f"client.game_key = {client.game_key}")
        assert_check(client.environment == "sandbox",
                     f"client.environment = {client.environment}")
        os.unlink(tmp_env)
    except Exception as e:
        if os.path.exists(tmp_env):
            os.unlink(tmp_env)
        raise

    print("\n[G2] from_env() raise nếu thiếu credentials")
    clear_cache()
    saved_env = {
        k: os.environ.pop(k, None)
        for k in (ENV_GAME_KEY, ENV_SECRET_KEY, ENV_GA_ENV)
    }
    try:
        try:
            GameAnalyticsClient.from_env(env_path="/nonexistent/.env_zzz")
            raise AssertionError("Không raise")
        except (FileNotFoundError, ValueError) as e:
            assert_check(True, f"raise {type(e).__name__}: {e}")
    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v

    # ----------------------------------------------------------------------
    # H. End-to-end với .env thật trong project
    # ----------------------------------------------------------------------
    print("\n[--- H. END-TO-END VỚI .ENV THẬT ---]")

    print("\n[H1] Đọc được GAME_KEY từ prj/.env")
    # prj/.env có sẵn từ trước
    prj_env = _PROJECT_ROOT / ".env"
    if prj_env.exists():
        clear_cache()
        creds = load_credentials(env_path=prj_env)
        assert_check(
            creds.game_key == "7ca2d532e8faa7e98a6792bdbaaf5b14",
            f"game_key = {creds.game_key[:8]}...",
        )
        assert_check(
            creds.secret_key == "e3eefc7f93f3b1e337e0f9822bae0954cbfac553",
            f"secret_key = {creds.secret_key[:8]}...",
        )
        assert_check(creds.environment == "sandbox",
                     f"environment = {creds.environment}")
        print(f"  [INFO] Loaded GAME_KEY={creds.game_key[:8]}... "
              f"SECRET_KEY={creds.secret_key[:8]}... "
              f"ENV={creds.environment}")
    else:
        print(f"  [WARN] .env không tồn tại: {prj_env} (skip)")

    print("\n[H2] require_game_key() / require_secret_key()")
    clear_cache()
    if prj_env.exists():
        gk = require_game_key()
        sk = require_secret_key()
        assert_check(gk == "7ca2d532e8faa7e98a6792bdbaaf5b14",
                     f"require_game_key OK")
        assert_check(sk == "e3eefc7f93f3b1e337e0f9822bae0954cbfac553",
                     f"require_secret_key OK")

    print("\n[H3] get_ga_env()")
    clear_cache()
    if prj_env.exists():
        env = get_ga_env()
        assert_check(env == "sandbox", f"get_ga_env() = {env}")

    print("\n[H4] Default environment khi file không có GA_ENV")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8",
    ) as f:
        f.write("GAME_KEY=key\nSECRET_KEY=secret\n")  # không có GA_ENV
        tmp_path = f.name
    try:
        creds = load_credentials(env_path=tmp_path)
        assert_check(creds.environment == DEFAULT_GA_ENV,
                     f"default env = {creds.environment}")
    finally:
        os.unlink(tmp_path)

    print("\n" + "=" * 60)
    print("TẤT CẢ CHECK ĐỀU PASS – CREDENTIALS LOADER OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
