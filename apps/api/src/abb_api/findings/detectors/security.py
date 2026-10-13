"""The six security detectors (ADR-131, Phase 13): secrets, credentials, destructive commands,
risky git, privileged SQL, unexpected destinations.

They run in the Phase 10 framework (same context, same `run_all`, same fingerprints and rebuild)
under category `security`, detector ids `sec.*`, kind `probable`. Evidence is the fixed rule
statement, a count, event ids, and for credentials artifact ids and kind names from a closed
vocabulary. A path, command, host, table name or secret value never reaches a finding: the
detectors decide on content (they run over the full events whatever role will read the finding)
and report only that a rule matched.

One finding per detector per run aggregates every matching event (`count`, and up to 48 event ids).
A detector listed in the project's `muted_detectors` produces nothing.
"""

import functools
from abc import abstractmethod
from collections.abc import Iterable
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.registry import PAYLOAD_REFERENCE_ATTRIBUTES

from abb_api.findings import security_words as w
from abb_api.findings.model import (
    Detector,
    DetectorContext,
    Finding,
    Kind,
    Severity,
)
from abb_api.findings.tokens import attr_str
from abb_api.security import rules
from abb_api.security.scanner import HIGH_CONFIDENCE_KINDS

EVIDENCE_EVENTS = 48  # `event_ids` bound in the evidence contract (MAX_IDS - 2)
MARKER = "[REDACTED:"
MAX_MARKERS_PER_STRING = 64
MAX_PAYLOAD_NODES = 2000
MAX_PAYLOAD_CHARS = 128 * 1024
MAX_PAYLOAD_DEPTH = 8
CACHE = 4096  # distinct command texts analysed per run

_SHELL_PREFIX = "shell.command."
_DB_PREFIX = "db.query."
_FILE_TYPES = frozenset({"file.read", "file.modified", "file.created"})
_SQL_PAYLOAD_KEYS = ("statement", "query", "sql")
_SQL_ATTRIBUTES = ("db.statement", "db.query.text")


class Hit:
    """One event (or artifact) that a rule matched, with the rule's fixed statement."""

    __slots__ = ("artifact_id", "event", "kinds", "level", "rule")

    def __init__(
        self,
        event: Event | None,
        rule: str,
        level: int = 0,
        artifact_id: str | None = None,
        kinds: Iterable[str] = (),
    ) -> None:
        self.event = event
        self.rule = rule
        self.level = level  # severity rank within the detector (0 = the base severity)
        self.artifact_id = artifact_id
        self.kinds = frozenset(kinds)  # credential kind names (closed vocabulary)


def _command(event: Event) -> str | None:
    if event.event_type.startswith(_SHELL_PREFIX):
        return attr_str(event, "shell.command")
    return None


def _once_per_span(events: Iterable[Event]) -> Iterable[Event]:
    """A shell command or query emits started and completed events with the same text; the first
    of each span stands for the call, so a count means calls."""
    seen: set[str] = set()
    for event in events:
        if event.span_id is not None and event.event_type.startswith((_SHELL_PREFIX, _DB_PREFIX)):
            if event.span_id in seen:
                continue
            seen.add(event.span_id)
        yield event


class SecurityDetector(Detector):
    """Shared shape: subclasses list their `hits`; this class turns them into one finding."""

    version = "1.0.0"
    info: w.SecurityDetectorInfo
    confidence = 0.8

    def __init__(self) -> None:
        self.name = self.info.id

    @abstractmethod
    def hits(self, ctx: DetectorContext) -> list[Hit]: ...

    def severity(self, hits: list[Hit]) -> Severity:
        return Severity(self.info.severities[0])

    def order_rules(self, rules_found: set[str]) -> list[str]:
        return sorted(rules_found)

    def extra_evidence(self, hits: list[Hit]) -> dict[str, Any]:
        return {}

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        if self.name in ctx.security.muted:
            return []
        found = self.hits(ctx)
        if not found:
            return []
        events = [h.event for h in found if h.event is not None]
        event_ids = list(dict.fromkeys(e.event_id for e in events))[:EVIDENCE_EVENTS]
        rules_found = self.order_rules({h.rule for h in found})
        evidence: dict[str, Any] = {
            "rule": rules_found[0],
            "rules": rules_found,
            # events and artifacts that matched (one event can match several rules)
            "count": len({h.event.event_id if h.event else h.artifact_id for h in found}),
            "event_ids": event_ids,
            **self.extra_evidence(found),
        }
        return [
            self.make(
                ctx,
                finding_type=self.info.finding_type,
                kind=Kind.PROBABLE,
                severity=self.severity(found),
                confidence=self.confidence,
                start_event_id=event_ids[0] if event_ids else None,
                end_event_id=event_ids[-1] if event_ids else None,
                key="",
                evidence=evidence,
            )
        ]


# --------------------------------------------------------------------------- secret file read


class SecretFileRead(SecurityDetector):
    info = w.SECRET_FILE_READ
    confidence = 0.8

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        found: list[Hit] = []
        check = functools.lru_cache(maxsize=CACHE)(rules.reads_secret_file)
        for event in _once_per_span(ctx.events):
            if event.event_type in _FILE_TYPES:
                path = attr_str(event, "file.path")
                if path is not None and rules.is_secret_path(path):
                    found.append(Hit(event, w.RULE_KEYFILE_EVENT))
                continue
            command = _command(event)
            if command is not None and check(command):
                found.append(Hit(event, w.RULE_KEYFILE_COMMAND))
        return found

    def order_rules(self, rules_found: set[str]) -> list[str]:
        order = [w.RULE_KEYFILE_EVENT, w.RULE_KEYFILE_COMMAND]
        return [r for r in order if r in rules_found]


# ----------------------------------------------------------------------- credential in output


def _marker_kinds(text: str) -> set[str]:
    """High-confidence kinds named by `[REDACTED:<kind>]` markers in a string. Plain string search,
    linear; the kind is read up to the closing bracket and kept only if it is a known kind."""
    kinds: set[str] = set()
    position = 0
    for _ in range(MAX_MARKERS_PER_STRING):
        start = text.find(MARKER, position)
        if start == -1:
            break
        begin = start + len(MARKER)
        end = text.find("]", begin, begin + 40)
        if end != -1 and text[begin:end] in HIGH_CONFIDENCE_KINDS:
            kinds.add(text[begin:end])
        position = begin
    return kinds


def _payload_kinds(payload: Any) -> set[str]:
    """Markers in a payload's string values, bounded in nodes, depth and characters."""
    kinds: set[str] = set()
    stack: list[tuple[Any, int]] = [(payload, 0)]
    nodes = chars = 0
    while stack and nodes < MAX_PAYLOAD_NODES and chars < MAX_PAYLOAD_CHARS:
        value, depth = stack.pop()
        nodes += 1
        if isinstance(value, str):
            chars += len(value)
            if MARKER in value:
                kinds |= _marker_kinds(value[: MAX_PAYLOAD_CHARS - chars + len(value)])
        elif depth < MAX_PAYLOAD_DEPTH:
            if isinstance(value, dict):
                stack.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list):
                stack.extend((v, depth + 1) for v in value)
    return kinds


def event_credential_kinds(event: Event) -> set[str]:
    """High-confidence credential kinds an event's redaction record or markers name."""
    kinds: set[str] = set()
    recorded = event.attributes.get("redaction.server_kinds")
    if isinstance(recorded, list):
        kinds |= {k for k in recorded[:32] if isinstance(k, str) and k in HIGH_CONFIDENCE_KINDS}
    for value in event.attributes.values():
        if isinstance(value, str) and MARKER in value:
            kinds |= _marker_kinds(value[:MAX_PAYLOAD_CHARS])
    if event.payload:
        kinds |= _payload_kinds(event.payload)
    return kinds


def _artifact_refs(event: Event) -> list[str]:
    refs: list[str] = []
    for key in PAYLOAD_REFERENCE_ATTRIBUTES:
        value = event.attributes.get(key)
        if isinstance(value, str) and value.startswith("artifact://"):
            refs.append(value[len("artifact://") :])
    return refs


class CredentialInOutput(SecurityDetector):
    info = w.CREDENTIAL_IN_OUTPUT
    confidence = 0.95

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        found: list[Hit] = []
        artifact_kinds = ctx.security.artifact_kinds
        referenced: dict[str, Event] = {}
        for event in ctx.events:
            event_kinds = event_credential_kinds(event)
            if event_kinds:
                found.append(Hit(event, w.RULE_CREDENTIAL, kinds=event_kinds))
            if artifact_kinds:
                for ref in _artifact_refs(event):
                    referenced.setdefault(ref, event)
        for artifact_id, names in artifact_kinds.items():
            usable = {k for k in names if k in HIGH_CONFIDENCE_KINDS}
            if usable:
                found.append(
                    Hit(referenced.get(artifact_id), w.RULE_CREDENTIAL, 0, artifact_id, usable)
                )
        return found

    def extra_evidence(self, hits: list[Hit]) -> dict[str, Any]:
        artifacts = [h.artifact_id for h in hits if h.artifact_id is not None]
        evidence: dict[str, Any] = {"kinds": sorted(set().union(*(h.kinds for h in hits)))}
        if artifacts:
            evidence["artifact_ids"] = artifacts[:EVIDENCE_EVENTS]
        return evidence


# ----------------------------------------------------------------------- destructive command


def _recorded_class(event: Event) -> int:
    value = attr_str(event, "shell.risk_class")
    if value is not None and len(value) == 2 and value[0] == "R" and value[1] in "01234":
        return int(value[1])
    return 0


class DestructiveCommand(SecurityDetector):
    """The recorded class (`shell.risk_class`) and a bounded server re-check, the stricter wins.

    A finding needs R4 (recorded or re-checked) or an R3-level pattern the re-check confirms. A
    recorded R3 that the re-check does not recognise (`rm file`, `git push`) is not reported: R3
    covers everyday operations and would bury the real ones.
    """

    info = w.DESTRUCTIVE_COMMAND
    confidence = 0.9

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        found: list[Hit] = []
        recheck = functools.lru_cache(maxsize=CACHE)(rules.destructive_level)
        for event in _once_per_span(ctx.events):
            command = _command(event)
            if command is None:
                continue
            recorded = _recorded_class(event)
            level = recheck(command)
            if recorded >= 4:
                found.append(Hit(event, w.RULE_R4_RECORDED, 4))
            elif level == rules.Destructive.R4:
                found.append(Hit(event, w.RULE_R4_RECHECK, 4))
            elif level == rules.Destructive.R3:
                rule = w.RULE_R3_CONFIRMED if recorded == 3 else w.RULE_R3_RECHECK
                found.append(Hit(event, rule, 3))
        return found

    def severity(self, hits: list[Hit]) -> Severity:
        return Severity.CRITICAL if any(h.level >= 4 for h in hits) else Severity.HIGH

    def order_rules(self, rules_found: set[str]) -> list[str]:
        order = [w.RULE_R4_RECORDED, w.RULE_R4_RECHECK, w.RULE_R3_CONFIRMED, w.RULE_R3_RECHECK]
        return [r for r in order if r in rules_found]


# ---------------------------------------------------------------------------------- risky git

_GIT_RULE_WORDS = {
    rules.GitRule.FORCE_PUSH: w.RULE_GIT_FORCE,
    rules.GitRule.PROTECTED_DELETE: w.RULE_GIT_PROTECTED_DELETE,
    rules.GitRule.PROTECTED_PUSH: w.RULE_GIT_PROTECTED_PUSH,
    rules.GitRule.HISTORY_REWRITE: w.RULE_GIT_REWRITE,
}


class RiskyGit(SecurityDetector):
    info = w.RISKY_GIT
    confidence = 0.85

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        found: list[Hit] = []
        analyse = functools.lru_cache(maxsize=CACHE)(rules.git_rules)
        for event in _once_per_span(ctx.events):
            if event.event_type == "git.push":
                if rules.is_protected_push_target(
                    attr_str(event, "git.push_target"), attr_str(event, "git.branch")
                ):
                    found.append(Hit(event, w.RULE_GIT_PROTECTED_PUSH))
                continue
            command = _command(event)
            if command is None or "git" not in command.lower():
                continue
            for rule in sorted(analyse(command)):
                found.append(Hit(event, _GIT_RULE_WORDS[rule]))
        return found

    def order_rules(self, rules_found: set[str]) -> list[str]:
        order = [_GIT_RULE_WORDS[r] for r in sorted(_GIT_RULE_WORDS)]
        return [r for r in order if r in rules_found]


# ---------------------------------------------------------------------------- privileged SQL


def _event_statement(event: Event) -> str | None:
    for key in _SQL_ATTRIBUTES:
        value = attr_str(event, key)
        if value:
            return value
    payload = event.payload
    if isinstance(payload, dict):
        for key in _SQL_PAYLOAD_KEYS:
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return None


class PrivilegedDb(SecurityDetector):
    info = w.PRIVILEGED_DB
    confidence = 0.85

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        found: list[Hit] = []
        check = functools.lru_cache(maxsize=CACHE)(rules.command_runs_privileged_sql)
        for event in _once_per_span(ctx.events):
            if event.event_type.startswith(_DB_PREFIX):
                if rules.event_sql_is_privileged(
                    attr_str(event, "db.operation"), _event_statement(event)
                ):
                    found.append(Hit(event, w.RULE_SQL_EVENT))
                continue
            command = _command(event)
            if command is not None and check(command):
                found.append(Hit(event, w.RULE_SQL_COMMAND))
        return found

    def order_rules(self, rules_found: set[str]) -> list[str]:
        return [r for r in (w.RULE_SQL_EVENT, w.RULE_SQL_COMMAND) if r in rules_found]


# ------------------------------------------------------------------- unexpected destination


class UnexpectedDestination(SecurityDetector):
    """Reports nothing without a destination allowlist: there is no honest baseline to guess."""

    info = w.UNEXPECTED_DESTINATION
    confidence = 0.7

    def hits(self, ctx: DetectorContext) -> list[Hit]:
        allowlist = ctx.security.destination_allowlist
        if not allowlist:
            return []
        found: list[Hit] = []
        hosts_of = functools.lru_cache(maxsize=CACHE)(rules.hosts_in_command)
        for event in _once_per_span(ctx.events):
            hosts: list[str] = []
            url = attr_str(event, "http.url")
            if url is not None:
                hosts.extend(rules.hosts_in_url(url))
            command = _command(event)
            if command is not None:
                hosts.extend(hosts_of(command))
            if any(not rules.host_allowed(h, allowlist) for h in hosts):
                found.append(Hit(event, w.RULE_DESTINATION))
        return found


SECURITY_DETECTORS: tuple[Detector, ...] = (
    SecretFileRead(),
    CredentialInOutput(),
    DestructiveCommand(),
    RiskyGit(),
    PrivilegedDb(),
    UnexpectedDestination(),
)
