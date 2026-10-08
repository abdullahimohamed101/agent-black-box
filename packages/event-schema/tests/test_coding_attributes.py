"""Coding-agent attributes (ADR-031): typed where known, optional everywhere."""

from typing import Any

import pytest
from abb_event_schema.errors import EventValidationError
from abb_event_schema.parse import parse_event_in

from tests.helpers import make_event

SHELL = {"shell.command": "python -m unittest"}


def shell_event(**attrs: Any) -> dict[str, Any]:
    return make_event(event_type="shell.command.failed", attributes={**SHELL, **attrs})


def test_a_test_run_event_with_artifacts_is_valid() -> None:
    event = parse_event_in(
        shell_event(
            **{
                "shell.exit_code": 1,
                "shell.risk_class": "R1",
                "shell.category": "MODIFY_FILES",
                "shell.stdout_artifact": "artifact://art_01J90000000000000000000031",
                "shell.stdout_bytes": 1832,
                "shell.output_truncated": False,
                "test.framework": "unittest",
                "test.suite": "tests",
                "test.total": 5,
                "test.failed": 1,
                "test.failing": ["tests.test_session.SessionTests.test_x"],
            }
        )
    )
    assert event.attributes["test.failed"] == 1


@pytest.mark.parametrize(
    "attrs",
    [
        {"shell.stdout_bytes": -1},
        {"shell.stdout_bytes": "12"},
        {"shell.output_truncated": "yes"},
        {"shell.category": 3},
        {"test.suite": ["a"]},
    ],
)
def test_wrong_types_are_rejected(attrs: dict[str, Any]) -> None:
    with pytest.raises(EventValidationError):
        parse_event_in(shell_event(**attrs))


def test_file_and_diff_attributes() -> None:
    event = parse_event_in(
        make_event(
            event_type="file.modified",
            span_id=None,
            attributes={
                "file.path": "app/session.py",
                "file.operation": "modified",
                "diff.artifact": "artifact://art_01J90000000000000000000030",
            },
        )
    )
    assert event.attributes["diff.artifact"].startswith("artifact://")
