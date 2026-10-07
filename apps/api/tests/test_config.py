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
