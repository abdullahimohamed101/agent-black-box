"""The alert payload: content-free and fixed-shape (ADR-150, spec §35).

The payload is built here and nowhere else, only from a `Subject` (stored facts: ids, names,
statuses, severities, counts, amounts, times) and a closed set of fixed strings. It never reads an
event, an attribute, an error message, a finding's evidence, a run's name or anything the viewer
rules treat as captured content. The link is a relative path; the sender makes it absolute from
`ABB_WEB_ORIGIN` (step 2), and the page it opens applies the viewer's role rules.

`TOP_LEVEL_KEYS` and `DETAIL_KEYS` are the closed sets; a test builds every trigger's payload from
a subject whose every free-text field holds a sentinel and checks that no value outside the
allowed names appears.
"""

import uuid
from datetime import datetime
from typing import Any, Final
from urllib.parse import quote

from abb_event_schema.ids import IdKind, from_uuid

from abb_api.alerts import model as m

TITLES: Final = {
    m.APPROVAL_REQUESTED: "Approval waiting",
    m.APPROVAL_EXPIRING: "Approval about to expire",
    m.SECURITY_FINDING: "Security finding",
    m.RUN_FAILED: "Run failed",
    m.SPEND_OVER: "Daily spend over threshold",
}
TOP_LEVEL_KEYS: Final = frozenset(
    {
        "alert_id", "kind", "severity", "title", "workspace_id", "project", "agent", "rule",
        "subject", "details", "link_path", "created_at",
    }
)  # fmt: skip
DETAIL_KEYS: Final = frozenset(
    {
        "run_status", "detector", "finding_severity", "expires_at", "seconds_to_expiry", "day",
        "spend_usd", "threshold_usd", "period",
    }
)  # fmt: skip
MAX_NAME: Final = 80


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(c for c in value if c.isprintable())[:MAX_NAME] or None


def link_path(subject: m.Subject) -> str:
    base = f"/w/{quote(subject.workspace_slug, safe='')}"
    project = quote(subject.project_slug, safe="") if subject.project_slug else None
    if subject.trigger in (m.APPROVAL_REQUESTED, m.APPROVAL_EXPIRING) and subject.approval_id:
        return f"{base}/approvals?approval={from_uuid(IdKind.APPROVAL, subject.approval_id)}"
    if subject.trigger == m.SECURITY_FINDING and project and subject.run_id and subject.finding_id:
        run = from_uuid(IdKind.RUN, subject.run_id)
        return (
            f"{base}/projects/{project}/runs/{run}"
            f"?finding={from_uuid(IdKind.FINDING, subject.finding_id)}"
        )
    if subject.trigger == m.RUN_FAILED and project and subject.run_id:
        return f"{base}/projects/{project}/runs/{from_uuid(IdKind.RUN, subject.run_id)}"
    if subject.trigger == m.SPEND_OVER and project:
        return f"{base}/projects/{project}/analytics"
    return base


def build_payload(
    subject: m.Subject, *, alert_id: uuid.UUID, rule: m.RuleRow, created_at: datetime
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "alert_id": from_uuid(IdKind.ALERT, alert_id),
        "kind": subject.trigger,
        "severity": subject.severity,
        "title": TITLES[subject.trigger],
        "workspace_id": from_uuid(IdKind.WORKSPACE, subject.workspace_id),
        "project": (
            {
                "id": from_uuid(IdKind.PROJECT, subject.project_id),
                "slug": subject.project_slug,
                "name": _name(subject.project_name),
            }
            if subject.project_id is not None
            else None
        ),
        "agent": (
            {"slug": subject.agent_slug, "name": _name(subject.agent_name)}
            if subject.agent_slug is not None
            else None
        ),
        "rule": {"id": from_uuid(IdKind.ALERT_RULE, rule.id), "name": _name(rule.name)},
        "subject": {
            key: from_uuid(kind, value)
            for key, kind, value in (
                ("run_id", IdKind.RUN, subject.run_id),
                ("finding_id", IdKind.FINDING, subject.finding_id),
                ("approval_id", IdKind.APPROVAL, subject.approval_id),
            )
            if value is not None
        },
        "details": {k: v for k, v in subject.details.items() if k in DETAIL_KEYS},
        "link_path": link_path(subject),
        "created_at": created_at.isoformat(),
    }
    return payload
