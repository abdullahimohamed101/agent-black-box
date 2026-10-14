"""The environment's quota defaults, from `Settings` (ABB_QUOTA_*)."""

from abb_api.core.config import Settings
from abb_api.quotas.model import QuotaLimits


def default_limits(settings: Settings) -> QuotaLimits:
    return QuotaLimits(
        agents=settings.quota_agents,
        runs_per_day=settings.quota_runs_per_day,
        events_per_day=settings.quota_events_per_day,
        artifact_bytes=settings.quota_artifact_bytes,
    )
