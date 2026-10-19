"""The fixed wording of the security detectors (ADR-131): the one place it lives.

A security finding states a category, a severity, a count, the rule that matched and the events
that show it. It never states a path, a command, a host, a table or a secret: every sentence here
is a constant, and the only variable parts (credential kind names) come from a closed vocabulary,
so a viewer who may not read content learns nothing from a finding that the finding's existence and
category do not already say. Tests plant sentinel strings and fail if one reaches any finding field.
"""

from dataclasses import dataclass

from abb_api.findings.model import SECURITY_PREFIX


@dataclass(frozen=True)
class SecurityDetectorInfo:
    id: str  # `sec.<finding_type>`: also the `detector` column and the id a project can mute
    finding_type: str
    title: str  # lower-case phrase; "Probable: " is added for the heading
    message: str  # the fixed sentence of the finding
    description: str  # what the detector looks for, for the settings and empty-state views
    severities: tuple[str, ...]  # ascending; most detectors have exactly one


SECRET_FILE_READ = SecurityDetectorInfo(
    id="sec.secret_file_read",
    finding_type="secret_file_read",
    title="a file that usually holds secrets was accessed",
    message="The agent read or changed a file whose name marks it as holding secrets.",
    description=(
        "A file event or a shell command touched a file named like a secret store: .env and "
        ".env.* (not .env.example), private keys and certificates, cloud and registry credential "
        "files, kubeconfig, secrets.*. Decided from the file name only."
    ),
    severities=("high",),
)
CREDENTIAL_IN_OUTPUT = SecurityDetectorInfo(
    id="sec.credential_in_output",
    finding_type="credential_in_output",
    title="a credential appeared in captured text",
    message="Captured text contained a credential; it was redacted before it was stored.",
    description=(
        "The server's scan, or the SDK's, replaced a provider-shaped credential (an AWS-style "
        "key, a private key, a token) in an event or an artifact. States the kind, never the value."
    ),
    severities=("critical",),
)
DESTRUCTIVE_COMMAND = SecurityDetectorInfo(
    id="sec.destructive_command",
    finding_type="destructive_command",
    title="a destructive command was run",
    message="The agent ran a command that can delete data or damage the system.",
    description=(
        "A shell command was classified R4, or the server's re-check found an unambiguous "
        "destructive pattern: recursive delete of a root, home or wildcard target, mkfs, dd to a "
        "device, DROP DATABASE, git clean -fdx. R3 is high, R4 is critical."
    ),
    severities=("high", "critical"),
)
RISKY_GIT = SecurityDetectorInfo(
    id="sec.risky_git",
    finding_type="risky_git",
    title="a risky git operation was run",
    message="The agent ran a git operation that forces a push, rewrites history or changes a "
    "protected branch.",
    description=(
        "A force push, a push to or a delete of main, master, production or release/*, or a "
        "history rewrite (filter-branch, reset --hard to an earlier commit, rebase -i), in a "
        "shell command or a git event."
    ),
    severities=("high",),
)
PRIVILEGED_DB = SecurityDetectorInfo(
    id="sec.privileged_db",
    finding_type="privileged_db",
    title="a privileged database statement was run",
    message="The agent ran a database statement that changes structure or changes rows without "
    "a filter.",
    description=(
        "SQL visible in a database event or passed to a database client: CREATE, ALTER, DROP, "
        "TRUNCATE, or DELETE or UPDATE with no WHERE clause."
    ),
    severities=("high",),
)
UNEXPECTED_DESTINATION = SecurityDetectorInfo(
    id="sec.unexpected_destination",
    finding_type="unexpected_destination",
    title="a host outside the allowlist was contacted",
    message="The agent contacted a host that is not on this project's allowlist.",
    description=(
        "A URL or a network command named a host that matches no entry of the project's "
        "destination allowlist. Reports nothing while the allowlist is empty; localhost is always "
        "allowed."
    ),
    severities=("medium",),
)

SECURITY_CATALOG: tuple[SecurityDetectorInfo, ...] = (
    SECRET_FILE_READ,
    CREDENTIAL_IN_OUTPUT,
    DESTRUCTIVE_COMMAND,
    RISKY_GIT,
    PRIVILEGED_DB,
    UNEXPECTED_DESTINATION,
)
SECURITY_DETECTOR_IDS: frozenset[str] = frozenset(d.id for d in SECURITY_CATALOG)
BY_FINDING_TYPE: dict[str, SecurityDetectorInfo] = {d.finding_type: d for d in SECURITY_CATALOG}

assert all(d.id == SECURITY_PREFIX + d.finding_type for d in SECURITY_CATALOG)

# Rule statements: what matched, in words, never what it matched on. A finding names the strongest
# rule that fired in `rule` and every rule that fired in `rules`.
RULE_KEYFILE_EVENT = "path matched a secret-file name"
RULE_KEYFILE_COMMAND = "command read a file whose name matches a secret-file name"
RULE_CREDENTIAL = "a redaction record names a credential pattern"
RULE_R4_RECORDED = "command was classified R4"
RULE_R4_RECHECK = "server re-check matched a destructive pattern (R4)"
RULE_R3_CONFIRMED = "command was classified R3 and the server re-check agreed"
RULE_R3_RECHECK = "server re-check matched a destructive pattern (R3)"
RULE_GIT_FORCE = "git push used a force option"
RULE_GIT_PROTECTED_DELETE = "a protected branch name was deleted"
RULE_GIT_PROTECTED_PUSH = "git push targeted a protected branch name"
RULE_GIT_REWRITE = "git command rewrites history"
RULE_SQL_EVENT = "SQL in a database event was DDL or changed rows without WHERE"
RULE_SQL_COMMAND = "SQL passed to a database client was DDL or changed rows without WHERE"
RULE_DESTINATION = "host is not on the project allowlist"

# Credential kinds in words (the scanner's closed vocabulary of high-confidence kinds).
KIND_WORDS: dict[str, str] = {
    "private_key": "private key",
    "aws_access_key": "AWS-style access key",
    "github_token": "GitHub token",
    "npm_token": "npm token",
    "gitlab_token": "GitLab token",
    "google_api_key": "Google API key",
    "google_oauth": "Google OAuth token",
    "slack_webhook": "Slack webhook",
    "slack_token": "Slack token",
    "api_key": "API key",
    "abb_api_key": "Agent BlackBox API key",
    "jwt": "JSON web token",
}
