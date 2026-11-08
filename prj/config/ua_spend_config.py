"""Cấu hình UA Spend – chi tiêu User Acquisition.

Theo BƯỚC 24 INSTRUCTION.md:

    **Mục tiêu:** Bổ sung dữ liệu chi tiêu acquisition để tính ROAS.

    Tạo ``data/ua_spend/ua_spend.csv`` với schema::

        date, platform, country, source, campaign,
        spend, impressions, clicks, installs

    **UA Spend KHÔNG phải player purchase event** — đây là nguồn riêng.

    Sau khi có cả Game Events + UA Spend, có thể tính::

        CPI   = spend / installs
        ROAS  = revenue / spend
        LTV:CAC
        Payback

    Acquisition sources mô phỏng::

        organic, facebook, google_ads, tiktok

    Campaigns mô phỏng::

        VN_AUG_01, VN_AUG_02, US_AUG_01, JP_AUG_01, ...

Module cung cấp:

    - ``UA_SOURCES``: 4 acquisition sources
    - ``UA_PLATFORMS``: 2 platforms
    - ``UA_COUNTRIES``: 6 top-tier countries
    - ``UA_CAMPAIGNS``: dict source → list campaign ids
    - ``UA_PLATFORM_WEIGHTS``: platform distribution
    - ``UA_COUNTRY_WEIGHTS``: country distribution
    - ``SPEND_RANGES``: spend per source (USD)
    - ``CTR_RANGES``: click-through rate per source
    - ``CVR_RANGES``: install conversion rate per source
    - ``CPI_FLOOR / CPI_CEILING``: bounding cho CPI
"""

from __future__ import annotations

# =====================================================================
# Acquisition sources (theo INSTRUCTION)
# =====================================================================
UA_SOURCES: tuple[str, ...] = (
    "organic",
    "facebook",
    "google_ads",
    "tiktok",
)

# Trọng số xuất hiện của source trong 1 ngày
UA_SOURCE_WEIGHTS: tuple[float, ...] = (0.30, 0.30, 0.25, 0.15)

# organic không có campaign — hệ thống auto-fill với None
# Paid sources có campaign id cụ thể
UA_CAMPAIGNS: dict[str, tuple[str | None, ...]] = {
    "organic": (None,),
    "facebook": (
        "VN_AUG_01", "US_AUG_01", "JP_AUG_01", "VN_SEP_01",
        "US_SEP_01", "KR_AUG_01", "ID_AUG_01",
    ),
    "google_ads": (
        "VN_AUG_02", "US_AUG_02", "JP_AUG_02", "KR_AUG_01",
        "TH_AUG_01", "VN_SEP_02",
    ),
    "tiktok": (
        "VN_AUG_03", "ID_AUG_01", "TH_AUG_01", "JP_AUG_02",
        "US_AUG_03",
    ),
}

# =====================================================================
# Platform & Country
# =====================================================================
UA_PLATFORMS: tuple[str, ...] = ("android", "ios")

# Phân bổ platform (android chiếm đa số thị trường mobile game)
UA_PLATFORM_WEIGHTS: tuple[float, ...] = (0.65, 0.35)

# Top-tier mobile game countries
UA_COUNTRIES: tuple[str, ...] = (
    "VN",  # Vietnam
    "US",  # United States
    "JP",  # Japan
    "KR",  # South Korea
    "ID",  # Indonesia
    "TH",  # Thailand
)

# Phân bổ country (VN + US chiếm đa số)
UA_COUNTRY_WEIGHTS: tuple[float, ...] = (
    0.35,  # VN
    0.25,  # US
    0.15,  # JP
    0.10,  # KR
    0.10,  # ID
    0.05,  # TH
)

# =====================================================================
# Spend ranges (USD / ngày / campaign)
# =====================================================================
# Tuỳ theo source và platform — paid ads đắt hơn organic (0)
SPEND_RANGES_USD: dict[str, tuple[float, float]] = {
    "organic": (0.0, 0.0),       # organic = không tốn tiền marketing
    "facebook": (200.0, 5000.0),  # paid social lớn
    "google_ads": (300.0, 7000.0),  # Google search/display
    "tiktok": (150.0, 3000.0),    # TikTok ads nhỏ hơn
}

# Hệ số platform (iOS thường đắt hơn Android ~30%)
PLATFORM_SPEND_MULTIPLIER: dict[str, float] = {
    "android": 1.0,
    "ios": 1.3,
}

# =====================================================================
# CTR (click-through rate) – clicks / impressions
# =====================================================================
# Industry benchmark: 1-5% cho mobile ads
CTR_RANGES: dict[str, tuple[float, float]] = {
    "organic": (0.0, 0.0),         # organic không qua impression funnel
    "facebook": (0.015, 0.045),    # 1.5% - 4.5%
    "google_ads": (0.020, 0.060),  # 2% - 6% (Google search cao hơn)
    "tiktok": (0.010, 0.040),      # 1% - 4%
}

# =====================================================================
# CVR (conversion rate) – installs / clicks
# =====================================================================
CVR_RANGES: dict[str, tuple[float, float]] = {
    "organic": (0.0, 0.0),         # không áp dụng
    "facebook": (0.10, 0.30),      # 10% - 30%
    "google_ads": (0.15, 0.40),    # 15% - 40% (search intent cao)
    "tiktok": (0.08, 0.25),        # 8% - 25%
}

# =====================================================================
# CPI bounds (USD / install)
# =====================================================================
# Industry benchmark hiện tại (2025-2026):
#   - Android: $0.5 - $3
#   - iOS:     $1.0 - $5
CPI_FLOOR: float = 0.10   # Minimum (organic gần như $0)
CPI_CEILING: float = 8.00  # Maximum (whale markets)

# =====================================================================
# Default date range
# =====================================================================
# Default simulation length cho UA Spend
DEFAULT_DATE_RANGE_DAYS: int = 30

# Format ISO date
DATE_FORMAT: str = "%Y-%m-%d"

# =====================================================================
# CSV schema (theo INSTRUCTION)
# =====================================================================
CSV_SCHEMA: tuple[str, ...] = (
    "date",
    "platform",
    "country",
    "source",
    "campaign",
    "spend",
    "impressions",
    "clicks",
    "installs",
)
CSV_DELIMITER: str = ","

# =====================================================================
# Output paths
# =====================================================================
# Tất cả các đường dẫn gốc sẽ được resolve tại simulator với project root.
DEFAULT_OUTPUT_DIR: str = "data/ua_spend"
DEFAULT_OUTPUT_FILE: str = "ua_spend.csv"
