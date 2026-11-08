"""Simulation package – logic mô phỏng cao cấp cho từng domain.

Hiện tại:
    - progression:  quản lý state & quyết định logic progression (Bước 9)
    - economy:      quản lý balance & Source/Sink cho virtual economy (Bước 10)
    - monetization: quản lý IAP & business event (Bước 11)
    - ad:           quản lý ad impression (rewarded/interstitial/banner) (Bước 12)
    - design:       quản lý 12 design event cố định (Bước 13)
    - error:        quản lý error event (Bước 14)
    - retention:    lifecycle / churn modeling (Bước 23)

Các simulator khác sẽ được bổ sung ở các bước sau theo INSTRUCTION.md.
"""

from __future__ import annotations

from .ad import (
    AD_PLACEMENTS,
    AdDecision,
    AdSimulator,
)
from .design import (
    DesignDecision,
    DesignSimulator,
)
from .economy import (
    CURRENCY_FIELD,
    EconomySimulator,
    ResourceTransaction,
)
from .error import ErrorDecision, ErrorSimulator
from .monetization import (
    MonetizationSimulator,
    PurchaseDecision,
)
from .progression import (
    ABANDON,
    COMPLETE,
    FAIL,
    START,
    ProgressionAction,
    ProgressionSimulator,
)
from .retention import (
    DailyRetention,
    RetentionCohort,
    RetentionCurve,
    RetentionSimulator,
    RetentionStats,
    create_retention_simulator,
)
from .ua_spend import (
    DailyUASpend,
    ROASAnalyzer,
    UASpendGenerator,
    UASpendRecord,
    UASpendStats,
    create_ua_spend_generator,
)

__all__ = [
    # Progression (Bước 9)
    "ProgressionAction",
    "ProgressionSimulator",
    "START",
    "FAIL",
    "COMPLETE",
    "ABANDON",
    # Economy (Bước 10)
    "EconomySimulator",
    "ResourceTransaction",
    "CURRENCY_FIELD",
    # Monetization (Bước 11)
    "MonetizationSimulator",
    "PurchaseDecision",
    # Ad (Bước 12)
    "AdSimulator",
    "AdDecision",
    "AD_PLACEMENTS",
    # Design (Bước 13)
    "DesignSimulator",
    "DesignDecision",
    # Error (Bước 14)
    "ErrorSimulator",
    "ErrorDecision",
    # Retention (Bước 23)
    "RetentionSimulator",
    "RetentionCurve",
    "RetentionCohort",
    "DailyRetention",
    "RetentionStats",
    "create_retention_simulator",
    # UA Spend (Bước 24)
    "UASpendGenerator",
    "UASpendRecord",
    "DailyUASpend",
    "UASpendStats",
    "ROASAnalyzer",
    "create_ua_spend_generator",
]
