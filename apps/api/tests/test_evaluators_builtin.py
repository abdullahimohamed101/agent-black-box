"""Built-in evaluators are pure, bounded and content-free (ADR-090, AC-4)."""

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from abb_event_schema.event import Event

from abb_api.evaluations.builtin import REGISTRY, WITHHELD_CONFIG_FIELDS, normalise_path
from abb_api.evaluations.protocol import (
    Budget,
    BudgetExceeded,
    EvaluationResult,
    InvalidResult,
    RunSnapshot,
    clean_result,
)
from abb_api.evaluations.snapshot import build_snapshot
from tests.authz.world import CONTENT_CANARIES
from tests.detector_helpers import to_event
from tests.ingest_helpers import make_run_ids, wire_event

STARTED = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)


def events(*specs: tuple[str, dict[str, Any]]) -> tuple[Event, ...]:
    run = make_run_ids()
    return tuple(
        to_event(wire_event(run, n, event_type=kind, attributes=attrs))
        for n, (kind, attrs) in enumerate(specs, start=1)
    )


def snapshot(
    *specs: tuple[str, dict[str, Any]], status: str = "SUCCESS", **summary: Any
) -> RunSnapshot:
    return RunSnapshot(
        run_id="run_x",
        status=status,
        started_at=STARTED,
        duration_ms=summary.pop("duration_ms", None),
        agent_slug="coding-agent",
        ordering_mode="sequence",
        summary=summary,
        events=events(*specs),
    )


def run(name: str, snap: RunSnapshot, **config: Any) -> EvaluationResult:
    impl = REGISTRY[name]
    cfg = impl.config_model.model_validate(config)
    result = impl.evaluate(snap, cfg, Budget(30))
    return clean_result(result, impl.metric_keys, frozenset(e.event_id for e in snap.events))


def edit(path: str, kind: str = "file.modified") -> tuple[str, dict[str, Any]]:
    return kind, {"file.path": path}


def suite(failed: int, total: int = 5) -> tuple[str, dict[str, Any]]:
    return "shell.command.completed", {
        "shell.command": "pytest",
        "test.framework": "pytest",
        "test.total": total,
        "test.failed": failed,
        "test.passed": total - failed,
    }


# ------------------------------------------------------------------ deterministic


def test_run_succeeded() -> None:
    assert run("run_succeeded", snapshot(status="SUCCESS")).label == "pass"
    failed = run("run_succeeded", snapshot(status="FAILED"))
    assert (failed.score, failed.label) == (0.0, "fail")
    assert failed.metrics == {"status": "FAILED"}


def test_tests_passed_uses_the_last_test_run_and_is_unknown_without_one() -> None:
    snap = snapshot(suite(2), edit("app/x.py"), suite(0))
    result = run("tests_passed", snap)
    assert (result.score, result.label) == (1.0, "pass")
    assert result.evidence_event_ids == (snap.events[2].event_id,)
    assert run("tests_passed", snapshot(suite(0), suite(3))).label == "fail"
    none = run("tests_passed", snapshot(edit("app/x.py")))
    assert (none.score, none.label) == (None, "unknown")
    other = ("shell.command.completed", {"shell.command": "ls"})  # no test framework
    assert run("tests_passed", snapshot(other)).label == "unknown"


def test_tests_passed_counts_a_suite_the_recorder_ended_as_failed() -> None:
    # a failing suite exits non-zero: the SDK ends its span in error (`shell.command.failed`)
    _, attrs = suite(2)
    snap = snapshot(suite(0), ("shell.command.failed", attrs))
    result = run("tests_passed", snap)
    assert (result.score, result.label) == (0.0, "fail")
    assert result.evidence_event_ids == (snap.events[1].event_id,)
    assert result.metrics["tests_failed"] == 2


def test_files_touched_only_with_allowed_and_forbidden_globs() -> None:
    ok = snapshot(edit("app/a.py"), edit("./app/sub/b.py", "file.created"))
    assert run("files_touched_only", ok, allowed_globs=["app/*"]).label == "pass"
    bad = snapshot(edit("app/a.py"), edit("config/prod.yml"), edit("app/../etc/passwd"))
    result = run("files_touched_only", bad, allowed_globs=["app/*"])
    assert (result.label, result.metrics) == ("fail", {"files_checked": 3, "violations": 2})
    assert result.evidence_event_ids == (bad.events[1].event_id, bad.events[2].event_id)
    forbidden = run("files_touched_only", ok, forbidden_globs=["app/sub/*"])
    assert forbidden.label == "fail" and forbidden.metrics["violations"] == 1
    # `**` is `*`; a backslash path is folded.
    assert (
        run("files_touched_only", snapshot(edit("app\\x\\y.py")), allowed_globs=["app/**"]).label
        == "pass"
    )
    assert normalise_path("a//b/./c/../d") == "a/b/d"
    # No file events at all passes vacuously.
    assert run("files_touched_only", snapshot(), allowed_globs=["x"]).label == "pass"


def test_no_policy_blocks() -> None:
    blocked = snapshot(("policy.action.blocked", {"policy.id": "p1"}))
    result = run("no_policy_blocks", blocked)
    assert (result.label, result.metrics) == ("fail", {"blocked": 1})
    assert len(result.evidence_event_ids) == 1
    assert run("no_policy_blocks", snapshot(edit("a"))).label == "pass"


def test_expected_files_changed() -> None:
    snap = snapshot(edit("app/a.py"), edit("docs/readme.md", "file.created"))
    assert (
        run("expected_files_changed", snap, required_paths=["app/a.py", "docs/readme.md"]).label
        == "pass"
    )
    missing = run("expected_files_changed", snap, required_paths=["app/a.py", "app/b.py"])
    assert (missing.label, missing.metrics) == ("fail", {"required": 2, "missing": 1})
    deleted = snapshot(edit("app/a.py", "file.deleted"))
    assert run("expected_files_changed", deleted, required_paths=["app/a.py"]).label == "fail"


# ---------------------------------------------------------------------- metrics


@pytest.mark.parametrize(
    ("name", "config", "summary", "label"),
    [
        ("cost_under", {"max_usd": 1.0}, {"estimated_cost_usd": 0.5}, "pass"),
        ("cost_under", {"max_usd": 1.0}, {"estimated_cost_usd": 1.5}, "fail"),
        ("cost_under", {"max_usd": 1.0}, {"estimated_cost_usd": 1.0}, "pass"),
        ("cost_under", {"max_usd": 1.0}, {}, "unknown"),
        ("latency_under", {"max_ms": 1000}, {"duration_ms": 900.0}, "pass"),
        ("latency_under", {"max_ms": 1000}, {"duration_ms": 1100.0}, "fail"),
        (
            "tool_error_rate_under",
            {"max_rate": 0.25},
            {"tool_spans_finished": 4, "tool_spans_ok": 4},
            "pass",
        ),
        (
            "tool_error_rate_under",
            {"max_rate": 0.25},
            {"tool_spans_finished": 4, "tool_spans_ok": 2},
            "fail",
        ),
        (
            "tool_error_rate_under",
            {"max_rate": 0.25},
            {"tool_spans_finished": 0, "tool_spans_ok": 0},
            "unknown",
        ),
        ("retries_under", {"max": 2}, {"retry_count": 2}, "pass"),
        ("retries_under", {"max": 2}, {"retry_count": 3}, "fail"),
        ("llm_calls_under", {"max": 5}, {"llm_calls": 6}, "fail"),
        ("llm_calls_under", {"max": 5}, {"llm_calls": True}, "unknown"),  # a bool is not a count
    ],
)
def test_metric_thresholds(
    name: str, config: dict[str, Any], summary: dict[str, Any], label: str
) -> None:
    result = run(name, snapshot(**summary), **config)
    assert result.label == label
    assert (result.score is None) == (label == "unknown")
    assert set(result.metrics) <= REGISTRY[name].metric_keys


# ------------------------------------------------- content-freedom and the canary run


def content_pair(
    path_a: str, path_b: str, cmd_a: str, cmd_b: str
) -> tuple[RunSnapshot, RunSnapshot]:
    def build(path: str, cmd: str) -> RunSnapshot:
        return snapshot(
            (
                "shell.command.completed",
                {"shell.command": cmd, "test.framework": "pytest", "test.failed": 1},
            ),
            edit("app/a.py"),
            edit(path),
            ("policy.action.blocked", {"policy.id": "p1", "policy.reason": cmd}),
            estimated_cost_usd=0.3, retry_count=1, llm_calls=2,
            tool_spans_finished=2, tool_spans_ok=1, duration_ms=500.0,
        )  # fmt: skip

    return build(path_a, cmd_a), build(path_b, cmd_b)


CONFIGS: dict[str, dict[str, Any]] = {
    "files_touched_only": {"allowed_globs": ["app/*"]},
    "expected_files_changed": {"required_paths": ["app/a.py"]},
    "cost_under": {"max_usd": 1},
    "latency_under": {"max_ms": 1000},
    "tool_error_rate_under": {"max_rate": 0.9},
    "retries_under": {"max": 5},
    "llm_calls_under": {"max": 5},
}


@pytest.mark.parametrize("name", [n for n in REGISTRY if n != "human"])
def test_two_snapshots_that_differ_only_in_content_give_identical_results(name: str) -> None:
    """The explanation and metrics are built from counts and templates, never attribute values."""
    a, b = content_pair("app/a.py", "app/z.py", "curl SECRET-1", "wget SECRET-2")
    ra = run(name, a, **CONFIGS.get(name, {}))
    rb = run(name, b, **CONFIGS.get(name, {}))
    assert (ra.score, ra.label, ra.explanation, dict(ra.metrics)) == (
        rb.score, rb.label, rb.explanation, dict(rb.metrics),
    )  # fmt: skip


@pytest.mark.parametrize("name", [n for n in REGISTRY if n != "human"])
def test_no_result_field_contains_a_content_canary(name: str) -> None:
    canary_events = [
        (
            "shell.command.completed",
            {"shell.command": CONTENT_CANARIES[0], "test.framework": "pytest", "test.failed": 1},
        ),
        edit(CONTENT_CANARIES[1]),
        edit(CONTENT_CANARIES[4], "file.created"),
        ("custom.thing.happened", {"thing.command": CONTENT_CANARIES[2]}),
        ("policy.action.blocked", {"policy.id": "p", "policy.reason": CONTENT_CANARIES[3]}),
    ]  # fmt: skip
    snap = snapshot(*canary_events, estimated_cost_usd=2.0)
    result = run(name, snap, **CONFIGS.get(name, {}))
    text = repr(
        (
            result.score,
            result.label,
            result.explanation,
            dict(result.metrics),
            result.evidence_event_ids,
        )
    )
    for canary in CONTENT_CANARIES:
        assert canary not in text


def test_every_builtin_declares_its_metric_keys_and_ids_belong_to_the_run() -> None:
    for name, impl in REGISTRY.items():
        assert isinstance(impl.metric_keys, frozenset), name
        assert impl.version >= 1 and impl.kind in ("deterministic", "metric", "human")
    snap = snapshot(("policy.action.blocked", {"policy.id": "p"}))
    result = run("no_policy_blocks", snap)
    assert set(result.evidence_event_ids) <= {e.event_id for e in snap.events}


def test_withheld_config_fields_cover_every_path_bearing_field() -> None:
    fields = {f for impl in REGISTRY.values() for f in impl.config_model.model_fields}
    pathy = {f for f in fields if "glob" in f or "path" in f}
    assert pathy <= WITHHELD_CONFIG_FIELDS


# ------------------------------------------------------------- the result contract


def good(**overrides: Any) -> EvaluationResult:
    base: dict[str, Any] = {"score": 1.0, "label": "pass", "explanation": "ok"}
    base.update(overrides)
    return EvaluationResult(**base)


@pytest.mark.parametrize(
    "result",
    [
        "not a result",
        good(score=1.5),
        good(score=float("nan")),
        good(score="1"),
        good(score=True),
        good(label=""),
        good(label="x" * 65),
        good(explanation="x" * 4001),
        good(explanation="\U0001d4d0" * 4000 + "x" * 1000),
        good(explanation=None),
        good(metrics={"unlisted": 1}),
        good(metrics={"known": {"nested": 1}}),
        good(metrics={"known": "x" * 257}),
        good(metrics={"known": float("inf")}),
        good(evidence_event_ids="evt_x"),
        good(metadata={"k": "x" * 9000}),
    ],
)
def test_clean_result_rejects_garbage_with_a_reason_that_carries_no_value(result: Any) -> None:
    with pytest.raises(InvalidResult) as exc:
        clean_result(result, frozenset({"known"}), frozenset())
    assert "xxxx" not in str(exc.value)


def test_clean_result_keeps_a_wide_explanation_inside_the_byte_limit() -> None:
    wide = "\U0001d4d0" * 4000  # 4,000 characters, 16,000 bytes
    assert clean_result(good(explanation=wide), frozenset(), frozenset()).explanation == wide


def test_clean_result_drops_foreign_evidence_and_caps_the_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = [f"evt_{n:026d}" for n in range(150)]
    result = clean_result(
        good(evidence_event_ids=("evt_FOREIGN", *ids, ids[0])), frozenset(), frozenset(ids)
    )
    assert result.evidence_event_ids == tuple(ids[:100])  # the foreign id came first: dropped
    assert "evt_FOREIGN" not in result.evidence_event_ids
    assert "FOREIGN" not in caplog.text


# ------------------------------------------------------------------------ budget


def test_the_budget_expires_and_check_raises() -> None:
    ticks = iter([0.0, 0.5, 1.5])
    budget = Budget(1.0, clock=lambda: next(ticks))
    assert not budget.expired()
    with pytest.raises(BudgetExceeded):
        budget.check()


def test_a_maximal_glob_workload_finishes_or_stops_on_its_budget() -> None:
    """32 maximal patterns over 50,000 long paths: bounded work, cooperative stop."""
    impl = REGISTRY["files_touched_only"]
    config = impl.config_model.model_validate(
        {"allowed_globs": [("*a" * 127) + "b" for _ in range(32)]}
    )
    run_ids = make_run_ids()
    long_path = "a" * 4000
    evs = tuple(
        to_event(
            wire_event(
                run_ids,
                n,
                event_type="file.modified",
                attributes={"file.path": long_path},
                span_id=...,
            )
        )
        for n in range(1, 3001)
    )
    snap = RunSnapshot("run_x", "SUCCESS", STARTED, None, None, "sequence", {}, evs)
    started = time.monotonic()
    try:
        impl.evaluate(snap, config, Budget(2.0))
    except BudgetExceeded:
        pass
    assert time.monotonic() - started < 6.0  # stopped on its own, no thread left behind


def test_a_run_is_evaluated_from_the_snapshot_builder_in_canonical_order() -> None:
    from abb_api.runs.queries import RunRecord

    run_ids = make_run_ids()
    late = to_event(
        wire_event(run_ids, 2, event_type="file.modified", attributes={"file.path": "b"})
    )
    early = to_event(
        wire_event(run_ids, 1, event_type="file.modified", attributes={"file.path": "a"})
    )
    record = RunRecord(
        id=uuid.uuid4(), project_id=uuid.uuid4(), name=None,
        status="SUCCESS", agent_slug="a", trace_id=uuid.uuid4(), started_at=STARTED,
        completed_at=None, duration_ms=5.0, ordering_mode="sequence", summary={"x": 1},
        summary_version=3, metadata={}, created_at=STARTED, updated_at=STARTED,
    )  # fmt: skip
    snap = build_snapshot(record, [late, early])  # arrival order is not canonical order
    assert [e.event_id for e in snap.events] == [early.event_id, late.event_id]
    assert snap.status == "SUCCESS" and snap.duration_ms == 5.0 and snap.run_id.startswith("run_")
