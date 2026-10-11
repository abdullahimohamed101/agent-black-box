"""Pure detectors over the committed pathological runs, bounds, hostile input and content hygiene.

The `*.expected.json` files next to the fixtures were written by hand from the scenarios in
`scripts/pathological_runs.py` (they name events by `sequence`), not from detector output.
"""

import hashlib
import json
import random
import re
import time
import traceback
from decimal import Decimal
from typing import Any

import pytest
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id

from abb_api.findings.build import build_context, fold_facts
from abb_api.findings.describe import describe
from abb_api.findings.detectors import DETECTORS, DETECTORS_VERSION, run_all
from abb_api.findings.model import (
    MAX_EVIDENCE_BYTES,
    MAX_LABEL,
    MAX_PER_DETECTOR,
    MAX_PER_RUN,
    Detector,
    DetectorContext,
    DetectorError,
    EvidenceError,
    Finding,
    Kind,
    Severity,
    fingerprint,
    validate_evidence,
)
from abb_api.runs.summary import derive_run
from tests.detector_helpers import (
    FIXTURE_NAMES,
    FIXTURES,
    Synth,
    context_for,
    findings_for,
    load_runs,
    load_script,
    run_cost,
    to_event,
)

CANARIES = (
    "CMDCANARY-curl-H-Bearer-s3cr3t",
    "/home/PATHCANARY/.ssh/id_rsa",
    "CUSTOMCANARY-rm-rf",
    "CWDCANARY",
    "RUNNAMECANARY",
    "TAGCANARY",
    "SUITECANARY",
    "REASONCANARY",
    "POLICYCANARY",
    "PAYLOADCANARY",
    "SPANNAMECANARY",
    "REPOCANARY",
    "URLCANARY",
    "FAILINGCANARY",
)


def dig(value: Any, path: str) -> Any:
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def same(key: str, actual: Any, wanted: Any) -> bool:
    if key.rsplit(".", 1)[-1] in ("cost_usd", "median_usd", "p95_usd"):
        return Decimal(str(actual)) == Decimal(str(wanted))
    if isinstance(wanted, float):
        return bool(actual == pytest.approx(wanted))
    return bool(actual == wanted)


# --------------------------------------------------------------------------- the fixtures


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_findings_match_the_hand_written_expectations(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text())
    ctx = context_for(name)
    by_seq = {e.sequence: e.event_id for e in ctx.events}
    produced = run_all(ctx)

    assert len(produced) == len(expected["findings"]), [
        (f.detector, f.finding_type, f.severity.value) for f in produced
    ]
    remaining = list(produced)
    for want in expected["findings"]:
        start = by_seq[want["start_seq"]] if want["start_seq"] is not None else None
        end = by_seq[want["end_seq"]] if want["end_seq"] is not None else None
        match = next(
            (
                f
                for f in remaining
                if (f.detector, f.finding_type, f.start_event_id, f.end_event_id)
                == (want["detector"], want["finding_type"], start, end)
            ),
            None,
        )
        assert match is not None, f"{name}: no {want['finding_type']} at {want['start_seq']}"
        remaining.remove(match)
        assert match.kind.value == want["kind"]
        assert match.severity.value == want["severity"]
        assert match.confidence == pytest.approx(want["confidence"])
        for key, wanted in want["evidence"].items():
            assert same(key, dig(match.evidence, key), wanted), f"{name}: {key}"
    assert not remaining


def test_the_healthy_run_yields_nothing_even_with_its_failures_retries_and_growing_context() -> (
    None
):
    ctx = context_for("healthy")
    assert ctx.facts.retry_count == 2 and ctx.facts.error_count >= 2
    assert run_all(ctx) == []


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_findings_do_not_depend_on_arrival_order(name: str) -> None:
    wires = list(load_runs(name).values())[-1]
    baseline = findings_for(name)
    for seed in range(5):
        shuffled = list(wires)
        random.Random(seed).shuffle(shuffled)
        assert findings_for(name, shuffled) == baseline


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_findings_obey_the_contract(name: str) -> None:
    ctx = context_for(name)
    for f in run_all(ctx):
        assert re.fullmatch(r"[a-z-]+@\d+\.\d+\.\d+", f.detector_version)
        assert f.detector_version.startswith(f.detector + "@")
        assert 0.0 <= f.confidence <= 1.0
        assert (f.kind is Kind.DETERMINISTIC) == (f.confidence == 1.0)
        assert set(f.evidence_event_ids) <= ctx.event_ids
        assert f.start_event_id is None or f.start_event_id in f.evidence_event_ids
        assert len(f.evidence_event_ids) <= 50 and re.fullmatch(r"[0-9a-f]{64}", f.fingerprint)
        if f.finding_type not in ("failure_chain", "probable_cause", "reported_loop"):
            assert f.evidence["thresholds"], f.finding_type
        title, message = describe(f)
        assert title.startswith("Probable:") == (f.kind is Kind.PROBABLE)
        assert message


def test_fixtures_are_current() -> None:
    rendered = load_script().render()
    assert set(rendered) == set(FIXTURE_NAMES)
    for name, text in rendered.items():
        assert (FIXTURES / f"{name}.ndjson").read_text() == text, name


def test_two_agents_report_only_the_looping_emitter() -> None:
    (finding,) = findings_for("two_agents")
    assert finding.evidence["agent_id"] == "agent-a"


def test_a_skewed_clock_is_judged_per_emitter() -> None:
    (finding,) = findings_for("skewed")
    assert finding.evidence["agent_id"] == "agent-a" and finding.evidence["retries"] == 3


def test_facts_folded_from_events_equal_the_summarizers() -> None:
    for name in FIXTURE_NAMES:
        for wires in load_runs(name).values():
            events = [to_event(w) for w in wires]
            ctx = build_context(events)
            derived = derive_run(events)
            f, s = ctx.facts, derived.summary
            assert f.status is derived.status and f.ordering_mode == derived.ordering_mode
            assert (f.event_count, f.error_count, f.retry_count) == (
                s["event_count"],
                s["error_count"],
                s["retry_count"],
            )
            assert (f.retries_unattributed, f.tool_spans_ok, f.tool_spans_finished) == (
                s["retries_unattributed"],
                s["tool_spans_ok"],
                s["tool_spans_finished"],
            )
            assert f.agent_slug == derived.agent_slug and f.started_at == derived.started_at


def test_a_failed_runs_terminal_event_is_the_one_that_set_the_status() -> None:
    ctx = context_for("failing")
    assert ctx.facts.terminal_event_id == ctx.events[-1].event_id
    assert ctx.facts.status.value == "FAILED"


# --------------------------------------------------------------------------- thresholds


def test_raising_a_threshold_silences_the_loop() -> None:
    """Mutation check: the looping fixture depends on `loop_min_repetitions`."""
    from abb_api.findings.model import DetectorConfig

    ctx = context_for("looping", config=DetectorConfig(loop_min_repetitions=20))
    assert not [
        f
        for f in run_all(ctx)
        if f.finding_type == "possible_loop" and "llm" in f.evidence["pattern"]
    ]


def test_an_active_run_with_a_loop_is_not_downgraded_to_info() -> None:
    synth = Synth()
    for _ in range(4):
        synth.tool("search")
        synth.tool("read")
    ctx = build_context(synth.events)
    (finding,) = run_all(ctx)
    assert finding.severity is Severity.WARNING  # RUNNING: not a successful run, so not info


def test_a_short_loop_on_a_successful_run_is_info() -> None:
    synth = Synth()
    synth.add("run.started")
    for _ in range(3):
        synth.tool("search")
        synth.tool("read")
    synth.add("run.completed", status="success")
    (finding,) = run_all(build_context(synth.events))
    assert finding.severity is Severity.INFO and finding.evidence["repetitions"] == 3


def test_a_block_that_is_itself_repeating_is_reported_by_its_shorter_form() -> None:
    synth = Synth()
    for _ in range(6):
        synth.tool("read")  # six of one token: below the single-token threshold of 8
    assert run_all(build_context(synth.events)) == []
    for _ in range(3):
        synth.tool("read")
    (finding,) = run_all(build_context(synth.events))
    assert finding.evidence["pattern"] == ["tool:read"] and finding.evidence["repetitions"] == 9


def test_absolute_retries_alone_are_info_and_a_burst_is_a_warning() -> None:
    spaced = Synth()
    for i in range(5):
        spans = spaced.tool(f"t{i}", ok=False).span_id
        spaced.add("retry.attempted", attrs={"retry.attempt": 1}, span=spans, at=None)
        spaced.n += 300  # five minutes between retries
    (spaced_finding,) = [
        f for f in run_all(build_context(spaced.events)) if f.finding_type == "retry_storm"
    ]
    assert spaced_finding.severity is Severity.INFO and spaced_finding.evidence["burst"] is False

    burst = Synth()
    span = burst.tool("t", ok=False).span_id
    for attempt in range(1, 4):
        burst.add("retry.attempted", attrs={"retry.attempt": attempt}, span=span)
    (burst_finding,) = run_all(build_context(burst.events))
    assert burst_finding.severity is Severity.WARNING and burst_finding.evidence["burst"] is True


def test_context_growth_is_a_rate_not_a_ratio() -> None:
    """Ordinary multi-turn growth (8k -> 24k in 8 calls) is no finding; a compounding one is."""
    ordinary = Synth()
    for i, tokens in enumerate((8_000, 10_000, 12_000, 14_000, 16_000, 18_000, 21_000, 24_000)):
        ordinary.llm(tokens)
        ordinary.tool(f"step{i}")
    assert run_all(build_context(ordinary.events)) == []

    big_but_linear = Synth()
    for tokens in (40_000, 60_000, 80_000, 100_000, 120_000):
        big_but_linear.llm(tokens)
    (finding,) = run_all(build_context(big_but_linear.events))
    assert finding.severity is Severity.WARNING and finding.evidence["superlinear"] is False

    compounding_small = Synth()
    for tokens in (1_000, 2_000, 6_000, 20_000, 60_000):
        compounding_small.llm(tokens)
    (small,) = run_all(build_context(compounding_small.events))
    assert small.severity is Severity.INFO and small.evidence["superlinear"] is True


def test_cost_spike_needs_a_finished_run_a_big_baseline_and_all_three_conditions() -> None:
    ctx = context_for("cost_spike")
    assert [f.finding_type for f in run_all(ctx)] == ["cost_spike"]
    running = ctx.events[:-2]  # no run.completed: still active
    assert run_all(build_context(running, cost_usd=ctx.facts.cost_usd, baseline=ctx.baseline)) == []
    assert ctx.baseline is not None
    for cost in (Decimal("0.03"), Decimal("0.0399"), Decimal("0.0")):
        quiet = build_context(ctx.events, cost_usd=cost, baseline=ctx.baseline)
        assert run_all(quiet) == []  # not above p95, not above 2x median, or under a cent
    assert (
        run_all(build_context(ctx.events, cost_usd=Decimal("0.1"), baseline=ctx.baseline))[
            0
        ].severity
        is Severity.WARNING
    )


def test_a_policy_blocked_run_and_a_timeout_have_their_own_categories() -> None:
    blocked = Synth()
    blocked.add("run.started")
    blocked.tool("deploy", ok=False)
    blocked.add("run.completed", status="blocked")
    chain = [f for f in run_all(build_context(blocked.events)) if f.finding_type == "failure_chain"]
    assert chain[0].evidence["category"] == "policy_termination"

    timed_out = Synth()
    timed_out.add("run.started")
    timed_out.tool("slow", ok=False)
    timed_out.add("run.completed", status="timeout")
    chain = [
        f for f in run_all(build_context(timed_out.events)) if f.finding_type == "failure_chain"
    ]
    assert chain[0].evidence["category"] == "timeout"


def test_probable_causes_use_whole_words() -> None:
    for error, expect in (("invalid_token", False), ("context_length_exceeded", True)):
        s = Synth()
        s.add("run.started")
        s.add(
            "llm.request.failed",
            status="error",
            attrs={"llm.provider": "p", "llm.model": "m", "llm.error_type": error},
            span=new_id(IdKind.SPAN),
        )
        s.add("run.failed")
        causes = [f for f in run_all(build_context(s.events)) if f.finding_type == "probable_cause"]
        assert bool(causes) is expect, error
        if expect:
            assert causes[0].evidence["cause"] == "context_overflow"
            assert causes[0].kind is Kind.PROBABLE


def test_a_cyclic_span_ancestry_does_not_hang_the_failure_chain() -> None:
    synth = Synth()
    synth.add("run.started")
    a, b = new_id(IdKind.SPAN), new_id(IdKind.SPAN)
    first = synth.add("tool.call.failed", status="error", attrs={"tool.name": "x"}, span=a)
    second = synth.add("tool.call.failed", status="error", attrs={"tool.name": "x"}, span=b)
    # Cyclic parents cannot be sent through the wire model (a span cannot be its own parent), but
    # two spans can name each other: a -> b -> a.
    objs = [
        first.model_copy(update={"parent_span_id": b}),
        second.model_copy(update={"parent_span_id": a}),
    ]
    synth.add("run.failed")
    ctx = build_context([synth.events[0], *objs, synth.events[-1]])
    assert [f for f in run_all(ctx) if f.finding_type == "failure_chain"]


# --------------------------------------------------------------------------- bounds


def test_bounds_on_a_run_engineered_to_produce_thousands_of_candidates() -> None:
    synth = Synth()
    for _ in range(5_000):  # one 10,000-token stretch: a single finding, not 5,000
        synth.tool("a")
        synth.tool("b")
    for i in range(80):  # 80 disjoint single-token loops
        for _ in range(8):
            synth.tool(f"loop{i}")
    for i in range(80):  # 80 groups of identical requests
        for _ in range(3):
            synth.tool(f"same{i}", payload={"n": i})
    for _ in range(80):  # 80 reported loops
        synth.add("loop.detected", attrs={"loop.pattern": ["x"], "loop.repetitions": 3})
    for i in range(80):  # 80 tool failure cascades, separated by successes
        for _ in range(3):
            synth.tool(f"bad{i}", ok=False)
        for _ in range(11):
            synth.tool(f"fine{i}")
    ctx = build_context(synth.events)
    found = run_all(ctx)
    per_detector: dict[str, int] = {}
    for f in found:
        per_detector[f.detector] = per_detector.get(f.detector, 0) + 1
    assert max(per_detector.values()) <= MAX_PER_DETECTOR
    assert len(found) <= MAX_PER_RUN
    assert per_detector["repeated-sequence"] == MAX_PER_DETECTOR
    assert per_detector["identical-request"] == MAX_PER_DETECTOR
    assert per_detector["reported-loop"] == MAX_PER_DETECTOR
    assert per_detector["tool-failure-cascade"] == MAX_PER_DETECTOR
    assert len(found) == MAX_PER_RUN  # four detectors at the cap, the rest dropped by severity
    sequence = [f for f in found if f.detector == "repeated-sequence"]
    assert max(f.evidence["repetitions"] for f in sequence) == 5_000
    assert len({f.fingerprint for f in found}) == len(found)


def strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def test_maximum_size_client_strings_cannot_make_evidence_too_big() -> None:
    huge = "x" * 4095  # one under the 4,096-character attribute limit, so a suffix fits
    synth = Synth()
    synth.add("run.started")
    for _ in range(12):  # a loop whose pattern tokens are 4,096-character tool names
        synth.tool(huge + "1")
        synth.tool(huge + "2")
    for _ in range(3):
        synth.tool(huge + "3", payload={"k": 1})
    for _ in range(3):
        synth.tool(huge + "4", ok=False)
    for _ in range(6):
        synth.llm(1)
    synth.add(
        "loop.detected",
        attrs={"loop.pattern": [huge[:4000]] * 64, "loop.repetitions": 5},
    )
    spans = [e.span_id for e in synth.events if e.event_type == "tool.call.failed"]
    for _ in range(3):
        synth.add("retry.attempted", attrs={"retry.attempt": 1}, span=spans[0])
    synth.add("run.failed")
    found = run_all(build_context(synth.events))
    assert {f.finding_type for f in found} >= {
        "possible_loop",
        "identical_request",
        "tool_failure_cascade",
        "reported_loop",
        "retry_storm",
        "failure_chain",
    }
    for f in found:
        size = len(json.dumps(f.evidence, ensure_ascii=False).encode())
        assert size < MAX_EVIDENCE_BYTES, f.finding_type
        assert all(len(s) <= MAX_LABEL for s in strings_in(f.evidence)), f.finding_type
        assert len(f.evidence_event_ids) <= 50
        title, message = describe(f)
        assert len(title) + len(message) < 4_000
    reported = next(f for f in found if f.finding_type == "reported_loop")
    assert len(reported.evidence["pattern"]) == 20


def test_validate_evidence_rejects_what_it_should() -> None:
    known = frozenset({"evt_A", "evt_B"})
    with pytest.raises(EvidenceError):
        validate_evidence("possible_loop", {"shell_command": "rm -rf /"})
    with pytest.raises(EvidenceError):
        validate_evidence("reported_loop", {"event_id": "evt_ZZ"}, known_event_ids=known)
    with pytest.raises(EvidenceError):
        validate_evidence("no_such_type", {})
    with pytest.raises(EvidenceError):
        validate_evidence("failure_chain", {"chain": [{"event_id": "evt_A", "command": "x"}]})
    clean, cited = validate_evidence(
        "probable_cause",
        {"cause": "c" * 500, "event_ids": ["evt_A"] * 3 + ["evt_B"], "tool_name": "t" * 300},
        known_event_ids=known,
    )
    assert len(clean["cause"]) == MAX_LABEL and len(clean["tool_name"]) == MAX_LABEL
    assert cited == ("evt_A", "evt_B")


def test_a_run_never_cites_more_than_fifty_events_per_finding() -> None:
    synth = Synth()
    synth.add("run.started")
    for _ in range(120):
        synth.tool("search")
        synth.tool("read")
    (finding,) = run_all(build_context(synth.events))
    assert finding.evidence["repetitions"] == 120
    assert len(finding.evidence["event_ids"]) == 48 and len(finding.evidence_event_ids) == 49


# --------------------------------------------------------------------------- hostile input


def test_hostile_attribute_values_are_skipped_not_raised_on() -> None:
    synth = Synth()
    synth.add("run.started")
    for tokens in (
        float("nan"),
        -5,
        "many",
        True,
        None,
        10**30,
        8_000,
        40_000,
        90_000,
        200_000,
        10**9,
    ):
        event = synth.llm(1)
        bad = event.model_copy(
            update={"attributes": {**event.attributes, "llm.input_tokens": tokens}}
        )
        synth.events[-1] = bad
    failed = synth.tool("x", ok=False)
    synth.events[-1] = failed.model_copy(
        update={
            "attributes": {**failed.attributes, "shell.exit_code": "two", "http.status_code": "x"}
        }
    )
    weird = synth.add("http.response", attrs={"http.status_code": 503})
    synth.events[-1] = weird.model_copy(update={"attributes": {"http.status_code": float("inf")}})
    loop = synth.add("loop.detected")
    synth.events[-1] = loop.model_copy(
        update={"attributes": {"loop.pattern": [1, 2, None], "loop.repetitions": "many"}}
    )
    run_all(build_context(synth.events))


# --------------------------------------------------------------------------- content hygiene


def with_canaries(wires: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for wire in wires:
        wire = json.loads(json.dumps(wire))
        wire["attributes"].update(
            {
                "shell.command": CANARIES[0],
                "file.path": CANARIES[1],
                "thing.command": CANARIES[2],
                "shell.cwd": CANARIES[3],
                "run.name": CANARIES[4],
                "test.suite": CANARIES[6],
                "retry.reason": CANARIES[7],
                "policy.reason": CANARIES[8],
                "span.name": CANARIES[10],
                "git.repo": CANARIES[11],
                "http.url": CANARIES[12],
                "test.failing": [CANARIES[13]],
            }
        )
        wire["tags"] = [CANARIES[5]]
        if wire["event_type"] != "tool.call.started" or "payload" not in wire:
            wire["payload"] = {"note": CANARIES[9]}
        out.append(wire)
    return out


@pytest.mark.parametrize("name", [n for n in FIXTURE_NAMES if n != "cost_spike"])
def test_no_content_reaches_a_finding_even_when_every_event_carries_it(name: str) -> None:
    wires = with_canaries(list(load_runs(name).values())[-1])
    ctx = context_for(name, wires)
    found = run_all(ctx)
    if name != "healthy":
        assert found
    digests = {
        hashlib.sha256(
            json.dumps(w["payload"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for w in wires
        if w.get("payload")
    }
    for f in found:
        title, message = describe(f)
        visible = json.dumps(
            {"evidence": f.evidence, "ids": f.evidence_event_ids, "t": title, "m": message}
        )
        everything = visible + f.fingerprint + repr(f)
        for canary in CANARIES:
            assert canary not in everything, (name, f.finding_type, canary)
        assert not re.search(r"[0-9a-f]{12,}", visible), visible[:200]
        assert not any(d in everything or d[:12] in everything for d in digests)


def test_canaries_in_identical_payloads_group_but_never_surface() -> None:
    synth = Synth()
    for _ in range(4):
        synth.tool("send", payload={"body": CANARIES[9]})
    (finding,) = run_all(build_context(synth.events))
    assert finding.evidence["count"] == 4
    assert CANARIES[9] not in repr(finding)


# --------------------------------------------------------------------------- run_all behaviour


class Raising(Detector):
    name = "raising"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        raise ValueError(CANARIES[0])


class Duplicating(Detector):
    name = "duplicating"
    version = "1.0.0"

    def detect(self, ctx: DetectorContext) -> list[Finding]:
        first = ctx.events[0].event_id
        return [
            self.make(
                ctx,
                finding_type="reported_loop",
                kind=Kind.DETERMINISTIC,
                severity=severity,
                confidence=1.0,
                start_event_id=first,
                end_event_id=first,
                key="same",
                evidence={"event_id": first},
            )
            for severity in (Severity.INFO, Severity.CRITICAL, Severity.WARNING)
        ]


def test_a_detector_bug_surfaces_as_a_fixed_message_without_the_cause() -> None:
    ctx = context_for("healthy")
    with pytest.raises(DetectorError) as caught:
        run_all(ctx, (Raising(),))
    error = caught.value
    assert str(error) == f"detector raising failed over {ctx.facts.event_count} events"
    assert error.__cause__ is None and error.__suppress_context__
    assert CANARIES[0] not in "".join(traceback.format_exception(error))


def test_an_evidence_violation_is_a_detector_error_too() -> None:
    class Leaky(Detector):
        name = "leaky"
        version = "1.0.0"

        def detect(self, ctx: DetectorContext) -> list[Finding]:
            return [
                self.make(
                    ctx,
                    finding_type="reported_loop",
                    kind=Kind.DETERMINISTIC,
                    severity=Severity.INFO,
                    confidence=1.0,
                    start_event_id=None,
                    end_event_id=None,
                    key="",
                    evidence={"shell_command": CANARIES[0]},
                )
            ]

    with pytest.raises(DetectorError) as caught:
        run_all(context_for("healthy"), (Leaky(),))
    assert CANARIES[0] not in "".join(traceback.format_exception(caught.value))


def test_duplicate_fingerprints_collapse_to_the_highest_severity() -> None:
    (only,) = run_all(context_for("healthy"), (Duplicating(),))
    assert only.severity is Severity.CRITICAL


def test_detector_versions_are_unique_and_versioned() -> None:
    assert DETECTORS_VERSION == 1
    assert len({d.name for d in DETECTORS}) == len(DETECTORS)
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", d.version) for d in DETECTORS)


# --------------------------------------------------------------------------- performance


def test_fifty_thousand_events_are_analysed_in_a_few_seconds() -> None:
    synth = Synth()
    synth.add("run.started")
    rng = random.Random(7)
    names = [f"tool{i}" for i in range(40)]
    while len(synth.events) < 50_000:
        roll = rng.random()
        if roll < 0.45:
            synth.tool(rng.choice(names), ok=rng.random() > 0.05, payload={"q": rng.randrange(500)})
        elif roll < 0.8:
            synth.llm(5_000 + rng.randrange(1_000))
        else:
            synth.add("file.modified", attrs={"file.path": "a.py"})
    built = time.perf_counter()
    ctx = build_context(synth.events)
    folded = time.perf_counter()
    run_all(ctx)
    done = time.perf_counter()
    print(
        f"build {folded - built:.2f}s detect {done - folded:.2f}s over {len(synth.events)} events"
    )
    assert done - folded < 3.0
    assert fold_facts(ctx.events, ctx.spans).event_count == len(synth.events)


def test_event_helper_types() -> None:
    assert isinstance(to_event(load_runs("healthy")[next(iter(load_runs("healthy")))][0]), Event)
    assert run_cost([to_event(w) for w in list(load_runs("cost_spike").values())[-1]]) == Decimal(
        "0.9"
    )


def test_the_identical_request_identity_is_independent_of_payload_content() -> None:
    """Security review P1-1: the fingerprint and the id derived from it are public, as is every
    other input of the hash, so a content-derived input would be an offline guessing oracle on
    withheld payloads. The same events with different payload bytes must give the same identity."""
    synth = Synth()
    for _ in range(4):
        synth.tool("read_file", payload={"path": "/etc/passwd"})
    guesses = ({"path": "/etc/passwd"}, {"path": "/etc/shadow"}, {"x": [1, 2, 3]})
    identities = set()
    for guess in guesses:
        events = [
            e.model_copy(update={"payload": guess}) if e.event_type == "tool.call.started" else e
            for e in synth.events
        ]
        (finding,) = run_all(build_context(events))
        assert finding.finding_type == "identical_request"
        identities.add(finding.fingerprint)  # the id is UUIDv5 over this, so it is equal too
    assert len(identities) == 1
    # And the fingerprint is exactly the hash of public inputs: a reader holding a guess learns
    # nothing from it because the guess is not an input.
    (finding,) = run_all(build_context(synth.events))
    public = "\x1f".join(("agent", "tool.call.started", "read_file"))
    assert finding.fingerprint == fingerprint(
        "identical-request", "identical_request", finding.start_event_id, public
    )
