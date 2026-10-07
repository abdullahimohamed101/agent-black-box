"""API key scopes (spec §92). SDK keys cannot administer a workspace."""

EVENTS_WRITE = "events:write"
RUNS_READ = "runs:read"
ARTIFACTS_WRITE = "artifacts:write"
POLICY_CHECK = "policy:check"

ALL_SCOPES = frozenset({EVENTS_WRITE, RUNS_READ, ARTIFACTS_WRITE, POLICY_CHECK})
