"""The analytics contract (INV-7). Controllers and services depend on this, never on SQL.

Every method takes an `AnalyticsScope`: no question can be asked without a workspace (INV-3).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from abb_api.analytics.schemas import (
    CostReport,
    PerformanceReport,
    ReliabilityReport,
    Summary,
)
from abb_api.tenancy import TenantContext

MAX_WINDOW_DAYS = 92
DEFAULT_WINDOW_DAYS = 7
DEFAULT_TOP = 10
MAX_TOP = 50


@dataclass(frozen=True)
class AnalyticsScope:
    """What a question is about. `project_id` and `agent_slug` narrow; the tenant never widens."""

    tenant: TenantContext
    start: datetime  # inclusive, on runs.started_at
    end: datetime  # exclusive
    project_id: uuid.UUID | None = None
    agent_slug: str | None = None


class AnalyticsStore(Protocol):
    async def summary(self, scope: AnalyticsScope) -> Summary: ...

    async def cost(self, scope: AnalyticsScope, *, top: int) -> CostReport: ...

    async def reliability(self, scope: AnalyticsScope, *, top: int) -> ReliabilityReport: ...

    async def performance(self, scope: AnalyticsScope, *, top: int) -> PerformanceReport: ...
