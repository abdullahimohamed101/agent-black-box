"""Tracing off (the default) costs nothing, and its rules hold without the optional extra.

Deterministic facts only: object identity, imports, call counts. No clock (D9 gate 5).
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from abb_api.core.config import Settings
from abb_api.main import create_app
from abb_api.obs import tracing
from abb_api.obs.catalogue import build_observability

SRC = Path(__file__).resolve().parents[2] / "src" / "abb_api"
GOOD = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"


@pytest.fixture(autouse=True)
def _off() -> None:
    tracing.install(None)


def test_a_span_while_off_is_one_shared_object_and_nothing_is_recorded() -> None:
    first = tracing.span("http.request", method="GET")
    second = tracing.span("job.run", link=GOOD, job_type="x", attempt=1)
    assert first is second is tracing.NOOP
    with first as entered:
        assert entered is tracing.NOOP
        entered.set(status=200, anything="at all")  # accepted and forgotten
    assert not hasattr(tracing.NOOP, "__dict__")  # __slots__: no per-span state can accumulate
    assert tracing.current_traceparent() is None
    assert tracing.enabled() is False


def test_our_code_imports_opentelemetry_in_exactly_one_module() -> None:
    importers = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(n == "opentelemetry" or n.startswith("opentelemetry.") for n in names):
                importers.add(str(path.relative_to(SRC)))
    assert importers == {"obs/tracing_otel.py"}


def test_creating_the_app_and_running_spans_imports_nothing_more_from_opentelemetry() -> None:
    """FastAPI itself depends on opentelemetry-api (and may import it); what we add must be zero.

    A child process: import fastapi first, snapshot, then everything of ours with tracing off.
    """
    code = textwrap.dedent(
        """
        import sys
        import fastapi  # noqa: F401
        before = {m for m in sys.modules if m.split(".")[0] == "opentelemetry"}
        from abb_api.core.config import Settings
        from abb_api.main import create_app
        from abb_api.obs import tracing
        app = create_app(Settings(database_url="postgresql+asyncpg://u:p@localhost/d"))
        with tracing.span("http.request", method="GET") as s:
            s.set(status=200)
        tracing.start(Settings(database_url="postgresql+asyncpg://u:p@localhost/d"),
                      service="x", metrics=None)
        after = {m for m in sys.modules if m.split(".")[0] == "opentelemetry"}
        assert after == before, sorted(after - before)
        assert "opentelemetry.sdk" not in sys.modules
        assert "abb_api.obs.tracing_otel" not in sys.modules
        print("clean")
        """
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "", "ABB_ENVIRONMENT": "development"},
    )
    assert result.returncode == 0 and result.stdout.strip() == "clean", result.stderr[-800:]


def test_enabling_without_the_extra_stops_startup_with_a_fixed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "abb_api.obs.tracing_otel", None)  # import raises ImportError
    settings = Settings(database_url="postgresql+asyncpg://u:p@h/d", otel_enabled=True)
    with pytest.raises(tracing.TracingUnavailable) as raised:
        tracing.start(settings, service="x", metrics=build_observability().catalogue)
    assert "otel" in str(raised.value) and "ABB_OTEL_ENABLED" in str(raised.value)
    assert tracing.enabled() is False


def test_settings_defaults_and_blank_values() -> None:
    settings = Settings(database_url="postgresql+asyncpg://u:p@h/d")
    assert (settings.otel_enabled, settings.otel_sample_ratio) == (False, 0.1)
    blank = Settings.model_validate(
        {"database_url": "postgresql+asyncpg://u:p@h/d", "otel_enabled": ""}
    )
    assert blank.otel_enabled is False  # Compose passes an empty string for an unset variable
    with pytest.raises(ValueError):
        Settings.model_validate(
            {"database_url": "postgresql+asyncpg://u:p@h/d", "otel_sample_ratio": 1.5}
        )


def test_the_frameworks_own_telemetry_is_switched_off() -> None:
    """FastAPI 0.142+ would export url.path, url.query and exception messages to any collector in
    the environment (see ADR-170). It must stay off however the environment is set."""
    app = create_app(Settings(database_url="postgresql+asyncpg://u:p@h/d"))
    config = getattr(app, "_telemetry", None)
    if config is None:
        pytest.skip("this FastAPI has no native telemetry")
    for key, value in tracing.LIBRARY_INSTRUMENTATION_DISABLED.items():
        assert config[key] is value, key


@pytest.mark.parametrize(
    "value",
    [
        None,
        42,
        "",
        GOOD[:-1],
        GOOD + "0",
        "01" + GOOD[2:],  # a version we do not read
        "00-" + "00" * 16 + "-" + "cd" * 8 + "-01",  # all-zero trace id
        "00-" + "ab" * 16 + "-" + "00" * 8 + "-01",  # all-zero span id
        "00-" + "AB" * 16 + "-" + "cd" * 8 + "-01",  # upper case is not W3C
        "00-" + "zz" * 16 + "-" + "cd" * 8 + "-01",
        "x" * 10_000,
    ],
)
def test_a_traceparent_from_a_job_payload_is_validated(value: object) -> None:
    assert tracing.valid_traceparent(value) is None
    assert tracing.traceparent_in({"traceparent": value}) is None


def test_a_good_traceparent_is_accepted_and_only_from_a_dict() -> None:
    assert tracing.valid_traceparent(GOOD) == GOOD
    assert tracing.traceparent_in({"traceparent": GOOD}) == GOOD
    assert tracing.traceparent_in(None) is None and tracing.traceparent_in([GOOD]) is None


def test_the_attribute_allow_list_is_closed() -> None:
    kept, refused = tracing.clean_attributes(
        {
            "method": "GET",
            "route": "/v1/runs/{run_id}",
            "status": 200,
            "job_type": "summarize_run",
            "outcome": "succeeded",
            "attempt": 2,
            "events": 3,
        }
    )
    assert refused == 0 and kept["http.route"] == "/v1/runs/{run_id}" and kept["abb.events"] == 3
    hostile = {
        "path": "/v1/runs/run_X",  # not a key
        "workspace_id": "ws_01",  # tenants are not attributes
        "route": "/v1/runs/run_01ABC?x=1",  # a raw path with a query is not a template
        "method": "GET /secret",
        "status": 99999,
        "outcome": "a payload value",
        "job_type": "x" * 65,  # a string longer than 64
        "error_type": "has a space and a message",
        "attempt": True,  # a bool is not a number
        "events": "7",
    }
    kept, refused = tracing.clean_attributes(hostile)
    assert kept == {} and refused == len(hostile)
    with pytest.raises(AssertionError):
        tracing.clean_attributes({"path": "/x"}, strict=True)


def test_every_string_attribute_validator_caps_length_at_64() -> None:
    for key, (_, accepts) in tracing.ATTRIBUTES.items():
        assert not accepts("a" * 65), key
        assert not accepts("/" + "a" * 64), key
        assert not accepts(None), key
