"""Stable, machine-readable validation errors.

Messages never include submitted values: telemetry may contain secrets (spec §93) and error
payloads end up in logs and HTTP responses.
"""

from dataclasses import dataclass, field
from enum import Enum

from pydantic import ValidationError


class ErrorCode(str, Enum):
    EVENT_INVALID = "EVENT_INVALID"
    EVENT_TOO_LARGE = "EVENT_TOO_LARGE"
    EVENT_SCHEMA_UNSUPPORTED = "EVENT_SCHEMA_UNSUPPORTED"


@dataclass(frozen=True)
class Issue:
    """One problem, located by field path."""

    loc: tuple[str | int, ...]
    code: str
    message: str


@dataclass
class EventValidationError(Exception):
    code: ErrorCode
    message: str
    issues: list[Issue] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(self.code.value, self.message)

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


def issues_from_validation_error(exc: ValidationError) -> list[Issue]:
    """Convert pydantic errors to stable issues without echoing submitted values.

    `include_input=False` drops the offending value; custom errors can refine the location
    through a `loc` context entry (model-level checks have no field path of their own).
    """
    issues: list[Issue] = []
    for err in exc.errors(include_url=False, include_context=True, include_input=False):
        loc: tuple[str | int, ...] = tuple(err["loc"])
        context = err.get("ctx") or {}
        if not loc and "loc" in context:
            loc = tuple(context["loc"])
        issues.append(Issue(loc=loc, code=str(err["type"]), message=str(err["msg"])))
    return issues
