"""Builders for detector tests: events from wire dicts and the pathological fixtures."""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from abb_event_schema.event import Event, finalize
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.parse import parse_event_in

from abb_api.cost.builtin import builtin_price_book
from abb_api.cost.engine import CostEngine
from abb_api.findings.build import build_context, make_baseline
from abb_api.findings.detectors import run_all
from abb_api.findings.model import DetectorConfig, DetectorContext, Finding
from abb_api.runs.summary import derive_run

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures" / "pathological"
SCRIPT = REPO / "scripts" / "pathological_runs.py"
WORKSPACE = new_id(IdKind.WORKSPACE)
PROJECT = new_id(IdKind.PROJECT)
RECEIVED = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
FIXTURE_NAMES = (
    "healthy",
    "looping",
    "identical",
    "retry_storm",
    "context_growth",
    "two_agents",
    "skewed",
    "cost_spike",
    "cascade",
    "failing",
)


def to_event(wire: dict[str, Any]) -> Event:
    return finalize(
        parse_event_in(wire), workspace_id=WORKSPACE, project_id=PROJECT, received_at=RECEIVED
    )


def load_runs(name: str) -> dict[str, list[dict[str, Any]]]:
    """The fixture's wire events grouped by run id, runs in file order."""
    runs: dict[str, list[dict[str, Any]]] = {}
    for line in (FIXTURES / f"{name}.ndjson").read_text().splitlines():
        wire = json.loads(line)
        runs.setdefault(wire["run_id"], []).append(wire)
    return runs


def run_cost(events: list[Event]) -> Decimal:
    derived = derive_run(events, CostEngine(builtin_price_book()))
    return sum((line.total for line in derived.cost_lines), Decimal(0))


def context_for(
    name: str, wires: list[dict[str, Any]] | None = None, config: DetectorConfig | None = None
) -> DetectorContext:
    """The detector context of a fixture's target run (the last run in the file)."""
    runs = list(load_runs(name).values())
    target = [to_event(w) for w in (wires if wires is not None else runs[-1])]
    baseline = None
    if name == "cost_spike":
        rows = [
            (min(e.occurred_at for e in events), run_cost(events))
            for events in ([to_event(w) for w in run] for run in runs[:-1])
        ]
        baseline = make_baseline(
            rows,
            agent_slug="spiky",
            window_days=30,
            min_runs=(config or DetectorConfig()).cost_spike_min_baseline,
        )
    return build_context(target, cost_usd=run_cost(target), baseline=baseline, config=config)


def findings_for(name: str, wires: list[dict[str, Any]] | None = None) -> list[Finding]:
    return run_all(context_for(name, wires))


def load_script() -> Any:
    spec = importlib.util.spec_from_file_location("pathological_runs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["pathological_runs"] = module
    spec.loader.exec_module(module)
    return module


class Synth:
    """Synthetic events of one run for bounds, hostile-input and benchmark tests."""

    def __init__(self, agent: str = "agent") -> None:
        self.run = new_id(IdKind.RUN)
        self.trace = new_id(IdKind.TRACE)
        self.agent = agent
        self.n = 0
        self.events: list[Event] = []
        self.root = new_id(IdKind.SPAN)

    def add(
        self,
        event_type: str,
        *,
        status: str | None = None,
        attrs: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        span: str | None = None,
        agent: str | None = None,
        tags: list[str] | None = None,
        at: datetime | None = None,
    ) -> Event:
        self.n += 1
        wire: dict[str, Any] = {
            "schema_version": "1.0",
            "event_id": new_id(IdKind.EVENT),
            "run_id": self.run,
            "trace_id": self.trace,
            "span_id": span,
            "agent_id": agent or self.agent,
            "event_type": event_type,
            "occurred_at": (at or T0 + timedelta(seconds=self.n))
            .isoformat()
            .replace("+00:00", "Z"),
            "sequence": self.n,
            "status": status,
            "attributes": attrs or {},
            "payload": payload,
            "tags": tags or [],
        }
        event = to_event({k: v for k, v in wire.items() if v is not None})
        self.events.append(event)
        return event

    def tool(
        self, name: str, *, ok: bool = True, payload: dict[str, Any] | None = None, **kw: Any
    ) -> Event:
        span = new_id(IdKind.SPAN)
        self.add("tool.call.started", attrs={"tool.name": name}, payload=payload, span=span)
        if ok:
            return self.add(
                "tool.call.completed", status="success", attrs={"tool.name": name}, span=span, **kw
            )
        return self.add(
            "tool.call.failed",
            status="error",
            attrs={"tool.name": name, "tool.error_type": "boom"},
            span=span,
            **kw,
        )

    def llm(self, tokens: int) -> Event:
        span = new_id(IdKind.SPAN)
        attrs = {"llm.provider": "p", "llm.model": "m"}
        self.add("llm.request.started", attrs=attrs, span=span)
        return self.add(
            "llm.request.completed",
            status="success",
            attrs={**attrs, "llm.input_tokens": tokens},
            span=span,
        )
