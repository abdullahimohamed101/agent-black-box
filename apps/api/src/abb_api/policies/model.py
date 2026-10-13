"""The policy value types (ADR-140): what a parsed policy is and what an evaluation takes and
returns. Pure data; parsing is in `parser.py`, evaluation in `evaluator.py`."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from abb_api.policies.glob import Glob

# Bounds on a policy document. The byte limit is checked before anything is parsed.
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_RULES = 200
MAX_GLOB_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 200
MAX_ATTRIBUTES_PER_RULE = 8
MAX_ATTRIBUTE_VALUE_LENGTH = 256
MAX_YAML_DEPTH = 8
MAX_YAML_NODES = 5_000
MIN_EXPIRES_SECONDS = 60
MAX_EXPIRES_SECONDS = 7 * 24 * 3600
DEFAULT_EXPIRES_SECONDS = 15 * 60

RISK_CLASSES = ("R0", "R1", "R2", "R3", "R4")
# Roles that may be named as approver: those that can read payloads (D4: the approver must see
# what they approve). `None` on a rule means "an owner or an admin".
APPROVER_ROLES = ("OWNER", "ADMIN", "DEVELOPER", "SECURITY")

AttributeValue = str | int | float | bool


class DecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class FailMode(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ExpiryAction(StrEnum):
    DENY = "deny"
    ALLOW = "allow"


@dataclass(frozen=True)
class When:
    """The conditions of a rule; every present condition must hold (AND)."""

    capability: str | None
    resource: Glob | None
    risk_classes: tuple[str, ...]  # empty: any
    attributes: tuple[tuple[str, AttributeValue], ...]  # sorted by key


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    when: When
    decision: DecisionKind
    approver_role: str | None = None  # None: owner or admin
    expires_in_seconds: int | None = None  # only for require_approval
    on_expiry: ExpiryAction | None = None  # only for require_approval
    on_error: FailMode | None = None  # None: derived from the risk class when asked (step 2)
    allow_self_approval: bool = False


@dataclass(frozen=True)
class Policy:
    """A validated policy. `version` is the stored version number (0: not stored, for example
    the built-in empty policy or a draft)."""

    rules: tuple[Rule, ...] = ()
    default: DecisionKind = DecisionKind.ALLOW
    version: int = 0

    def with_version(self, version: int) -> "Policy":
        return Policy(self.rules, self.default, version)


EMPTY_POLICY = Policy()


@dataclass(frozen=True)
class Intent:
    """What an agent is about to do (spec §88). `resource` and `attributes` are matched, never
    stored or echoed by the evaluator."""

    capability: str
    resource: str = ""
    risk_class: str | None = None
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    decision: DecisionKind
    rule_id: str | None  # None: no rule matched, the policy default applied
    policy_version: int
    reason: str  # fixed text from the rule's own description or id, never from the intent
