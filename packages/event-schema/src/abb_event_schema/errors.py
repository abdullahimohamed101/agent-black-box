"""Stable, machine-readable validation errors.

Messages never include submitted values: telemetry may contain secrets (spec §93) and error
payloads end up in logs and HTTP responses.
"""

from dataclasses import dataclass, field
from enum import Enum


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

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"
