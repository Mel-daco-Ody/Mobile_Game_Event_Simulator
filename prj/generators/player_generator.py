"""Player Generator – sinh tập players khởi đầu cho simulation.

Theo BƯỚC 3 trong INSTRUCTION.md (section 15 của README):
    - Phân bổ theo segment:
        Payer:       Non-Payer / Low-Payer / Mid-Payer / Whale
        Engagement:  Casual / Regular / Engaged / Hardcore
        Lifecycle:   Early Churn / Normal Retention / High Retention
    - user_id ổn định theo format 'player_000001', 'player_000002', ...
    - Cùng player giữ nguyên user_id qua mọi session.

Module này cung cấp:
    - generate_players(count, simulation_start_date, seed)
        Sinh list[Player] với phân bổ theo weights từ player_config.
    - generate_one_player(idx, simulation_start_date, rng)
        Hàm nội bộ, sinh một player theo index (1-based để format user_id).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from config import game_config, player_config
from models import Player


def _weighted_choice(
    rng: random.Random,
    options: list[str],
    weights: list[float],
) -> str:
    """Chọn ngẫu nhiên một phần tử theo weights.

    Args:
        rng: instance random.Random (để có thể tái lập được khi seed).
        options: danh sách tuỳ chọn.
        weights: trọng số tương ứng (cộng lại phải xấp xỉ 1.0).

    Returns:
        Một phần tử trong options.
    """
    if len(options) != len(weights):
        raise ValueError(
            f"options ({len(options)}) và weights ({len(weights)}) phải cùng độ dài"
        )
    return rng.choices(options, weights=weights, k=1)[0]


def _pick_platform(rng: random.Random) -> str:
    """Chọn platform (android/ios) theo weight."""
    return _weighted_choice(rng, game_config.PLATFORMS, game_config.PLATFORM_WEIGHTS)


def _pick_country(rng: random.Random) -> str:
    """Chọn country theo weight."""
    return _weighted_choice(rng, game_config.COUNTRIES, game_config.COUNTRY_WEIGHTS)


def _pick_device_and_os(rng: random.Random, platform: str) -> tuple[str, str, str]:
    """Chọn (device, manufacturer, os_version) phù hợp với platform.

    Args:
        rng: random.Random instance.
        platform: 'android' hoặc 'ios'.

    Returns:
        (device, manufacturer, os_version)
    """
    if platform not in game_config.DEVICE_MODELS:
        raise ValueError(f"Platform không được hỗ trợ: {platform}")

    manufacturer = rng.choice(game_config.MANUFACTURERS_BY_PLATFORM[platform])
    device = rng.choice(game_config.DEVICE_MODELS[platform])
    os_version = rng.choice(game_config.OS_VERSIONS[platform])

    return device, manufacturer, os_version


def _pick_acquisition(rng: random.Random) -> tuple[str, str | None]:
    """Chọn (acquisition_source, campaign_id).

    Quy tắc:
        - 'organic' -> campaign_id = None
        - các nguồn paid -> chọn campaign ngẫu nhiên từ CAMPAIGNS[source]
    """
    source = _weighted_choice(
        rng,
        player_config.ACQUISITION_SOURCES,
        player_config.ACQUISITION_SOURCE_WEIGHTS,
    )
    campaign_pool = player_config.CAMPAIGNS.get(source, [None])
    campaign_id = rng.choice(campaign_pool)  # có thể là None cho organic
    return source, campaign_id


def _pick_install_date(
    rng: random.Random,
    simulation_start_date: datetime,
    max_days_back: int = 30,
) -> datetime:
    """Random install_date trong khoảng [start - max_days_back, start].

    Mục đích: mô phỏng player đã cài từ trước khi simulation bắt đầu,
    giúp cohort & retention D1/D3/D7/... có dữ liệu ngay từ ngày đầu.

    Lưu ý: tính tổng offset (ngày + giây) rồi mới trừ một lần,
    tránh trường hợp timedelta(days=N) - timedelta(seconds=M) vượt quá N ngày.
    """
    if max_days_back < 0:
        raise ValueError("max_days_back phải >= 0")
    # Offset tổng nằm trong [0, max_days_back * 86400] giây
    total_seconds_back = rng.randint(0, max_days_back * 86_400)
    return simulation_start_date - timedelta(seconds=total_seconds_back)


def generate_one_player(
    idx: int,
    simulation_start_date: datetime,
    rng: random.Random,
) -> Player:
    """Sinh một player theo index (1-based).

    Args:
        idx: số thứ tự 1-based của player, dùng để format user_id.
             user_id = f"player_{idx:06d}" (ví dụ player_000001).
        simulation_start_date: ngày bắt đầu simulation (timezone-aware).
        rng: instance random.Random để đảm bảo có thể tái lập kết quả.

    Returns:
        Một Player mới, chưa từng có session nào (session_count=0).
    """
    if idx < 1:
        raise ValueError(f"idx phải >= 1, nhận được: {idx}")

    # Phân bổ 3 trục segment độc lập
    payer_seg = _weighted_choice(
        rng,
        player_config.PAYER_SEGMENTS,
        player_config.PAYER_SEGMENT_WEIGHTS,
    )
    engagement_seg = _weighted_choice(
        rng,
        player_config.ENGAGEMENT_SEGMENTS,
        player_config.ENGAGEMENT_SEGMENT_WEIGHTS,
    )
    churn_seg = _weighted_choice(
        rng,
        player_config.LIFECYCLE_SEGMENTS,
        player_config.LIFECYCLE_SEGMENT_WEIGHTS,
    )

    # Platform / country / device phù hợp
    platform = _pick_platform(rng)
    country = _pick_country(rng)
    device, manufacturer, os_version = _pick_device_and_os(rng, platform)

    # Acquisition source + campaign
    acquisition_source, campaign_id = _pick_acquisition(rng)

    # Install date rải đều trong khoảng 30 ngày gần simulation_start_date
    install_date = _pick_install_date(rng, simulation_start_date)

    # Lấy default balance từ config
    init_bal = player_config.INITIAL_PLAYER_BALANCE

    return Player(
        user_id=f"player_{idx:06d}",
        country=country,
        platform=platform,
        device=device,
        manufacturer=manufacturer,
        os_version=os_version,
        install_date=install_date,
        acquisition_source=acquisition_source,
        campaign_id=campaign_id,
        player_segment=payer_seg,
        engagement_segment=engagement_seg,
        churn_segment=churn_seg,
        current_level=player_config.INITIAL_PLAYER_LEVEL,
        current_world=1,
        current_attempt_num=1,
        coins=init_bal["coins"],
        gems=init_bal["gems"],
        energy=init_bal["energy"],
        lives=init_bal["lives"],
        stars=init_bal["stars"],
        tickets=init_bal["tickets"],
        session_count=0,
        transaction_num=0,
        lifetime_revenue=0,
        churn_status="active",
        is_new_install=True,
    )


def generate_players(
    count: int,
    simulation_start_date: datetime | None = None,
    seed: int | None = 42,
) -> list[Player]:
    """Sinh danh sách player khởi đầu cho simulation.

    Args:
        count: số lượng player cần sinh. Theo INSTRUCTION section 29:
               100 (Phase 1) → 100K (Phase 4).
        simulation_start_date: ngày bắt đầu simulation. Mặc định = hiện tại (UTC).
        seed: seed cho random. Mặc định 42 để kết quả tái lập được.
              Đặt None nếu muốn random thực sự.

    Returns:
        list[Player] có length = count, user_id từ player_000001 đến player_NNNNNN.
    """
    if count < 0:
        raise ValueError(f"count phải >= 0, nhận được: {count}")

    # Mặc định simulation_start_date = hiện tại, timezone-aware
    if simulation_start_date is None:
        simulation_start_date = datetime.now(tz=timezone.utc)
    elif simulation_start_date.tzinfo is None:
        # Ép UTC nếu người dùng truyền naive datetime
        simulation_start_date = simulation_start_date.replace(tzinfo=timezone.utc)

    # Tạo RNG riêng để có thể seed/tái lập
    rng = random.Random(seed)

    return [
        generate_one_player(idx=i + 1, simulation_start_date=simulation_start_date, rng=rng)
        for i in range(count)
    ]


def summarize_players(players: list[Player]) -> dict[str, Any]:
    """Thống kê nhanh phân bổ segment của một tập player (chỉ để in/log).

    Returns:
        dict với các key: payer, engagement, lifecycle, platform, country,
        acquisition_source.
    """
    summary: dict[str, dict[str, int]] = {
        "payer": {},
        "engagement": {},
        "lifecycle": {},
        "platform": {},
        "country": {},
        "acquisition_source": {},
    }
    for p in players:
        for key, value in (
            ("payer", p.player_segment),
            ("engagement", p.engagement_segment),
            ("lifecycle", p.churn_segment),
            ("platform", p.platform),
            ("country", p.country),
            ("acquisition_source", p.acquisition_source),
        ):
            summary[key][value] = summary[key].get(value, 0) + 1
    return summary
