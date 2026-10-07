"""Schema versioning (spec §64.5, §102): MAJOR.MINOR, minors only add optional fields."""

import re

from abb_event_schema.errors import ErrorCode, EventValidationError, Issue

SCHEMA_VERSION = "1.0"
SUPPORTED_MAJOR = 1

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")


def parse_schema_version(value: str) -> tuple[int, int]:
    match = _VERSION_RE.fullmatch(value)
    if not match:
        raise EventValidationError(
            ErrorCode.EVENT_SCHEMA_UNSUPPORTED,
            "schema_version must look like MAJOR.MINOR.",
            [Issue(("schema_version",), "version_malformed", "Expected MAJOR.MINOR.")],
        )
    return int(match.group(1)), int(match.group(2))


def check_supported(value: str) -> None:
    """Accept any 1.x event: older servers ignore the optional fields a newer minor adds."""
    major, _ = parse_schema_version(value)
    if major != SUPPORTED_MAJOR:
        raise EventValidationError(
            ErrorCode.EVENT_SCHEMA_UNSUPPORTED,
            f"Schema version {value} is not accepted (supported major: {SUPPORTED_MAJOR}).",
            [Issue(("schema_version",), "version_unsupported", "Unsupported major version.")],
        )
