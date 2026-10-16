"""The retention policy as pure data: floor, ceiling, ordering, defaults and cutoffs."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from abb_api.core.config import Settings
from abb_api.retention import model as m
from abb_api.retention.model import FOREVER, RetentionInvalid, Tier

DEFAULTS = {Tier.ARTIFACTS: 30, Tier.TRACE: 90, Tier.RUN: 365, Tier.AUDIT: 365}


def test_a_workspace_without_choices_has_the_defaults() -> None:
    assert m.validate(DEFAULTS, {}) == DEFAULTS


def test_a_choice_replaces_the_default_and_forever_is_explicit() -> None:
    days = m.validate(DEFAULTS, {Tier.AUDIT: FOREVER, Tier.ARTIFACTS: 14})
    assert days == {Tier.ARTIFACTS: 14, Tier.TRACE: 90, Tier.RUN: 365, Tier.AUDIT: None}


@pytest.mark.parametrize(("value", "ok"), [(6, False), (7, True), (36500, True), (36501, False)])
def test_the_floor_is_seven_days_and_the_ceiling_a_century(value: int, ok: bool) -> None:
    if ok:
        m.check_setting(Tier.AUDIT, value)
    else:
        with pytest.raises(RetentionInvalid) as error:
            m.check_setting(Tier.AUDIT, value)
        assert error.value.reason == ("below_floor" if value < 7 else "above_ceiling")
        assert str(value) not in str(error.value)  # a message never repeats the value


@pytest.mark.parametrize(
    ("stored", "offender"),
    [
        ({Tier.ARTIFACTS: 100}, Tier.ARTIFACTS),  # longer than trace detail (90)
        ({Tier.TRACE: 400}, Tier.TRACE),  # longer than the run record (365)
        ({Tier.TRACE: FOREVER}, Tier.TRACE),  # forever inside a finite run record
        ({Tier.ARTIFACTS: FOREVER}, Tier.ARTIFACTS),
    ],
)
def test_an_inner_tier_never_outlives_an_outer_one(
    stored: dict[Tier, m.TierSetting], offender: Tier
) -> None:
    with pytest.raises(RetentionInvalid) as error:
        m.validate(DEFAULTS, stored)
    assert (error.value.tier, error.value.reason) == (offender, "order")


def test_everything_forever_is_valid_and_the_audit_log_is_independent() -> None:
    forever: dict[Tier, m.TierSetting] = {
        Tier.ARTIFACTS: FOREVER,
        Tier.TRACE: FOREVER,
        Tier.RUN: FOREVER,
    }
    assert m.validate(DEFAULTS, forever)[Tier.RUN] is None
    # The audit log has no ordering rule in either direction.
    assert m.validate(DEFAULTS, {Tier.AUDIT: 7})[Tier.AUDIT] == 7
    assert m.validate(DEFAULTS, {Tier.AUDIT: 3650})[Tier.AUDIT] == 3650


def test_the_cutoff_is_exactly_the_given_number_of_days_before_now() -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert m.cutoff(now, 30) == now - timedelta(days=30)
    assert m.cutoff(now, None) is None


def test_defaults_come_from_the_environment_settings() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://x",
        retention_trace_days="forever",
        retention_run_days="forever",
    )
    assert m.defaults_of(settings) == {
        Tier.ARTIFACTS: 30, Tier.TRACE: None, Tier.RUN: None, Tier.AUDIT: 365,
    }  # fmt: skip


@pytest.mark.parametrize(
    "overrides",
    [
        {"retention_artifacts_days": 6},
        {"retention_trace_days": 0},
        {"retention_run_days": 40000},
        {"retention_audit_days": "never"},
        {"retention_artifacts_days": 100, "retention_trace_days": 50},
        {"retention_trace_days": 400},
        {"retention_trace_days": "forever"},
        {"retention_mode": "delete"},
    ],
)
def test_bad_environment_defaults_stop_the_process_at_startup(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+asyncpg://x", **overrides)  # type: ignore[arg-type]


def test_the_default_mode_is_dry_run() -> None:
    settings = Settings(database_url="postgresql+asyncpg://x")
    assert settings.retention_mode == "dry_run"
    assert (
        settings.retention_artifacts_days,
        settings.retention_trace_days,
        settings.retention_run_days,
        settings.retention_audit_days,
    ) == (30, 90, 365, 365)


@pytest.mark.parametrize("mode", ["off", "dry_run", "enforce"])
def test_every_mode_is_a_valid_setting(mode: str) -> None:
    built = Settings.model_validate(
        {"database_url": "postgresql+asyncpg://x", "retention_mode": mode}
    )
    assert built.retention_mode == mode
