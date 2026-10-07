"""Typed enums shared by every consumer of the contract (INV-6)."""

from enum import Enum


class EventStatus(str, Enum):
    """Outcome carried by completion-style events. Point events may omit it."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class SpanKind(str, Enum):
    """Span categories (spec §63.5)."""

    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    SHELL = "shell"
    DB = "db"
    HTTP = "http"
    FILE = "file"
    GIT = "git"
    RETRIEVAL = "retrieval"
    EVALUATION = "evaluation"
    CUSTOM = "custom"


class EventClass(str, Enum):
    """Coarse grouping used by timeline filters (spec §9.1)."""

    RUN = "run"
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    FILE = "file"
    GIT = "git"
    SHELL = "shell"
    DATABASE = "database"
    NETWORK = "network"
    RELIABILITY = "reliability"
    SECURITY = "security"
    APPROVAL = "approval"
    SPAN = "span"
    CUSTOM = "custom"


class Priority(int, Enum):
    """SDK drop priority under buffer pressure (spec §67.4): lower number is dropped last."""

    P0 = 0
    P1 = 1
    P2 = 2


class RunStatus(str, Enum):
    """Run lifecycle (spec §9.8, §153)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    BLOCKED = "BLOCKED"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_RUN_STATUSES


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCESS,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
        RunStatus.BLOCKED,
    }
)

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.SUCCESS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
            RunStatus.BLOCKED,
        }
    ),
    RunStatus.WAITING: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
    ),
    RunStatus.WAITING_FOR_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
    ),
}


def can_transition(current: RunStatus, new: RunStatus) -> bool:
    """True if the run state machine allows current -> new. Terminal states allow nothing."""
    return new in _RUN_TRANSITIONS.get(current, frozenset())
