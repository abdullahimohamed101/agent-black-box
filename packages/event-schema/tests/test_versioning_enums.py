import pytest

from abb_event_schema.enums import RunStatus, can_transition
from abb_event_schema.errors import ErrorCode, EventValidationError
from abb_event_schema.versioning import check_supported, parse_schema_version


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.7", "1.9999"])
def test_any_1x_is_accepted(version: str) -> None:
    check_supported(version)


@pytest.mark.parametrize("version", ["2.0", "0.9", "10.0"])
def test_other_majors_are_unsupported(version: str) -> None:
    with pytest.raises(EventValidationError) as exc:
        check_supported(version)
    assert exc.value.code is ErrorCode.EVENT_SCHEMA_UNSUPPORTED


@pytest.mark.parametrize(
    "version", ["", "1", "1.", "1.0.0", "v1.0", "01.0", "1.-1", "one.two", "1.0 "]
)
def test_malformed_versions_are_unsupported(version: str) -> None:
    with pytest.raises(EventValidationError) as exc:
        parse_schema_version(version)
    assert exc.value.code is ErrorCode.EVENT_SCHEMA_UNSUPPORTED


def test_run_state_machine() -> None:
    assert can_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    assert can_transition(RunStatus.RUNNING, RunStatus.WAITING_FOR_APPROVAL)
    assert can_transition(RunStatus.WAITING_FOR_APPROVAL, RunStatus.RUNNING)
    assert can_transition(RunStatus.RUNNING, RunStatus.SUCCESS)


def test_terminal_states_allow_nothing() -> None:
    for status in RunStatus:
        if status.is_terminal:
            assert not any(can_transition(status, other) for other in RunStatus)


def test_invalid_transitions() -> None:
    assert not can_transition(RunStatus.QUEUED, RunStatus.SUCCESS)
    assert not can_transition(RunStatus.WAITING, RunStatus.SUCCESS)
    assert not can_transition(RunStatus.RUNNING, RunStatus.RUNNING)
