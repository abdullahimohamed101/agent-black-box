import pytest
from pydantic import ValidationError

from abb_api.core.config import Settings


def test_missing_database_url_fails_fast_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("ABB_DATABASE_URL", raising=False)
    monkeypatch.chdir(str(tmp_path))  # no .env here
    with pytest.raises(ValidationError) as exc:
        Settings()  # type: ignore[call-arg]
    assert "database_url" in str(exc.value)


def test_cors_origins_parse() -> None:
    s = Settings(database_url="x", cors_origins="http://a, http://b ,")
    assert s.cors_origin_list == ["http://a", "http://b"]


def test_the_evaluator_timeout_must_leave_the_lease_heartbeat_room() -> None:
    assert Settings(database_url="x").eval_timeout_seconds < 60 / 2  # the default fits
    with pytest.raises(ValidationError, match="eval_timeout_seconds"):
        Settings(database_url="x", worker_lease_seconds=60, eval_timeout_seconds=30)
    assert Settings(database_url="x", worker_lease_seconds=60, eval_timeout_seconds=29)
    # An unset timeout follows a short lease instead of refusing to start.
    assert Settings(database_url="x", worker_lease_seconds=1).eval_timeout_seconds == 0.4


def test_a_fake_judge_is_refused_in_production_unless_allowed() -> None:
    with pytest.raises(ValidationError, match="judge_provider"):
        Settings(database_url="x", environment="production", judge_provider="fake")
    allowed = Settings(
        database_url="x", environment="production", judge_provider="fake", judge_allow_fake=True
    )
    assert allowed.judge_provider == "fake"
    assert Settings(database_url="x", environment="production").judge_provider == "none"
    assert Settings(database_url="x", environment="test", judge_provider="fake")
