"""Run comparison: normaliser, steps, alignment, the demo pair in both projections (AC-6, AC-7)."""

import importlib.util
import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from abb_event_schema.event import Event

from abb_api.comparison.align import align
from abb_api.comparison.compare import Comparison, SideInput, compare_runs
from abb_api.comparison.normalise import normalise
from abb_api.comparison.steps import extract_steps, is_step
from tests.detector_helpers import REPO, to_event
from tests.ingest_helpers import make_run_ids, wire_event

FIXTURE = Path(__file__).parent / "fixtures" / "compare_pair"
STARTED = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
MAX_STEPS, MAX_EDITS = 2_000, 600


# ------------------------------------------------------------------ the normaliser (B-27)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pytest   -q\t tests/", "pytest -q tests/"),  # whitespace collapses
        ("README.md", "readme.md"),  # lower-cased: README and readme merge, by design
        ("open evt_01M4QNE6M0HEM4JTH54PQ1E812", "open #"),  # prefixed ULID
        ("run 01M4QNE6M0HEM4JTH54PQ1E812 done", "run # done"),  # bare ULID
        ("id 3f2504e0-4f89-11d3-9a0c-0305e82c3301", "id #"),  # UUID
        ("at 2026-10-12T09:00:01.250Z end", "at # end"),  # ISO timestamp
        ("at 2026-10-12 09:00:01 end", "at # end"),
        ("took 1.5s then 250ms and 2m", "took # then # and #"),  # durations by grammar
        ("elapsed 00:01:23", "elapsed #"),
        ("commit 3f2a9c1d77e0b5a4", "commit #"),  # hex with digits
        ("deadbeef", "deadbeef"),  # an all-letter hex word is a word
        ("blob_deadbeef", "blob_#"),  # ...unless a boundary marks it as a value
        ("/obj/deadbeef", "/obj/#"),
        ("addr 0xdeadbeef", "addr #"),
        ("serve localhost:8080", "serve localhost:#"),  # port
        ("line 12345", "line #"),  # digit runs of four or more
        ("python3.11 -m pytest", "python3.11 -m pytest"),  # short digits stay
        ("tail -n 3300 logs/x.log", "tail -n # logs/x.log"),
        ("cd /tmp/tmpab12cd/repo && ls", "cd /tmp/repo && ls"),  # scratch dir
        ("cd /private/var/folders/ab/cd12/T/tmp9x/repo", "cd /tmp/repo"),
    ],
)
def test_the_normaliser_table(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def test_workdir_prefixes_are_dropped_where_they_start_a_path() -> None:
    assert normalise("/work/a1/app/x.py", ["/work/a1"]) == normalise(
        "/work/b2/app/x.py", ["/work/b2"]
    )
    assert normalise("cat /work/a1/app/x.py", ["/work/a1"]) == "cat /app/x.py"
    assert normalise("echo /work/a1x/y", ["/work/a1"]) == "echo /work/a1x/y"  # not a path prefix


def test_the_normaliser_is_bounded_on_hostile_input() -> None:
    hostile = ("0" * 200 + "a" * 200 + "-") * 5_000  # a million characters
    start = time.perf_counter()
    out = normalise(hostile)
    assert len(out) < 3_000 and time.perf_counter() - start < 1.0


# ------------------------------------------------------------------ alignment, the pure part


def lcs(a: list[int], b: list[int]) -> int:
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            table[i][j] = (
                table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
            )
    return table[0][0]


def check_alignment(a: list[Any], b: list[Any], result: Any) -> int:
    """Every step appears once, in order, matched pairs have equal keys; returns the edit count."""
    lefts = [p.left for p in result.pairs if p.left is not None]
    rights = [p.right for p in result.pairs if p.right is not None]
    assert lefts == list(range(len(a))) and rights == list(range(len(b)))
    for p in result.pairs:
        if p.matched:
            assert p.left is not None and p.right is not None and a[p.left] == b[p.right]
    return sum(p.left is not None for p in result.pairs if not p.matched) + sum(
        p.right is not None for p in result.pairs if not p.matched
    )


def test_identical_sequences_are_all_matched() -> None:
    keys = [("tool", "a"), ("shell", None), ("file.modified", None)] * 4
    result = align(keys, keys, max_edits=MAX_EDITS)
    assert result.quality == "full" and all(p.matched for p in result.pairs)


def test_alignment_is_optimal_valid_and_symmetric_over_seeded_random_sequences() -> None:
    rng = random.Random(11)
    for _ in range(400):
        a = [rng.choice("abc") for _ in range(rng.randint(0, 25))]
        b = [rng.choice("abc") for _ in range(rng.randint(0, 25))]
        forward, backward = align(a, b, max_edits=1_000), align(b, a, max_edits=1_000)
        assert forward.quality == backward.quality == "full"
        edits = check_alignment(a, b, forward)
        ints_a = [ord(c) for c in a]
        ints_b = [ord(c) for c in b]
        assert edits == len(a) + len(b) - 2 * lcs(ints_a, ints_b)  # minimal
        mirrored = tuple((p.right, p.left, p.matched) for p in backward.pairs)
        assert tuple((p.left, p.right, p.matched) for p in forward.pairs) == mirrored


def test_a_replacement_is_zipped_into_changed_pairs() -> None:
    result = align(["x", "A", "B", "y"], ["x", "C", "y"], max_edits=100)
    rows = [(p.left, p.right, p.matched) for p in result.pairs]
    assert rows == [(0, 0, True), (1, 1, False), (2, None, False), (3, 2, True)]


def test_the_first_divergence_is_the_end_of_the_common_prefix_whatever_the_budget() -> None:
    rng = random.Random(5)
    prefix = [rng.choice("abc") for _ in range(300)]
    a = prefix + [rng.choice("abc") for _ in range(2_000)]
    b = prefix + [rng.choice("abc") for _ in range(2_000)]
    a[300], b[300] = "x", "y"
    result = align(a, b, max_edits=MAX_EDITS)
    first = next(i for i, p in enumerate(result.pairs) if not p.matched)
    assert result.quality == "partial" and first == 300


def timed(a: list[Any], b: list[Any], max_edits: int = MAX_EDITS) -> tuple[Any, float]:
    start = time.perf_counter()
    result = align(a, b, max_edits=max_edits)
    return result, time.perf_counter() - start


def test_the_hard_inputs_answer_within_the_budget() -> None:
    """AC-7: 2,000-step pairs. Disjoint, shifted, 3-key random, alternating with an insertion."""
    n = 2_000
    rng = random.Random(7)
    cases: dict[str, tuple[list[Any], list[Any]]] = {
        "disjoint": ([("k", i) for i in range(n)], [("j", i) for i in range(n)]),
        "shifted": ([("k", i) for i in range(n)], [("k", i) for i in range(1, n + 1)]),
        "random3": (
            [rng.choice("abc") for _ in range(n)],
            [rng.choice("abc") for _ in range(n)],
        ),
    }
    alternating = ["a", "b", "c"] * (n // 3)
    inserted = [*alternating[:1000], "z", *alternating[1000:]]
    cases["alternating_insert"] = (alternating, inserted)
    for name, (a, b) in cases.items():
        result, seconds = timed(a, b)
        assert seconds < 3.0, (name, seconds)
        check_alignment(a, b, result)
    quality = {name: timed(a, b)[0].quality for name, (a, b) in cases.items()}
    assert quality == {
        "disjoint": "partial",
        "shifted": "full",
        "random3": "partial",
        "alternating_insert": "full",
    }


def test_twenty_thousand_random_steps_answer_partial_within_the_budget_not_a_timeout() -> None:
    """AC-7 mutation: ABB_COMPARE_MAX_STEPS raised to 20,000 must still be bounded by the edit
    and work budgets."""
    rng = random.Random(3)
    a = [rng.choice("abc") for _ in range(20_000)]
    b = [rng.choice("abc") for _ in range(20_000)]
    result, seconds = timed(a, b)
    assert result.quality == "partial" and seconds < 3.0, seconds


# ------------------------------------------------------------------ steps and runs


Spec = tuple[Any, ...]  # (event type, attributes[, status])


def synth(
    *specs: Spec,
    start: datetime = STARTED,
    shuffle: random.Random | None = None,
    skew: timedelta = timedelta(0),
) -> list[Event]:
    run = make_run_ids()
    wires = []
    for n, spec in enumerate(specs, start=1):
        kind, attrs = spec[0], spec[1]
        wire = wire_event(
            run, n, event_type=kind, attributes=attrs,
            occurred_at=(start + skew + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
            status=spec[2] if len(spec) > 2 else None,
        )  # fmt: skip
        wires.append(wire)
    if shuffle:
        shuffle.shuffle(wires)
    return [to_event(w) for w in wires]


def side(events: list[Event], status: str = "SUCCESS", **summary: Any) -> SideInput:
    return SideInput(
        run_id="run_x", status=status, started_at=STARTED, summary=summary, events=events,
        event_count=int(summary.get("event_count", len(events))),
    )  # fmt: skip


def compare(
    a: SideInput,
    b: SideInput,
    *,
    content: bool = True,
    steps: int = MAX_STEPS,
    edits: int = MAX_EDITS,
) -> Comparison:
    return compare_runs(a, b, content=content, max_steps=steps, max_edits=edits)


def tool(name: str, status: str = "success") -> Spec:
    return "tool.call.completed", {"tool.name": name}, status


def test_which_events_are_steps() -> None:
    steps = [
        "tool.call.completed", "llm.request.failed", "shell.command.completed", "file.modified",
        "git.push", "retry.attempted", "policy.action.blocked", "approval.granted",
        "span.completed", "agent.completed", "db.query.completed",
    ]  # fmt: skip
    nots = [
        "tool.call.started", "run.started", "run.completed", "http.request", "custom.thing",
        "llm.request.started", "agent.started", "agent.state_changed", "span.started",
    ]  # fmt: skip
    assert all(is_step(t) for t in steps) and not any(is_step(t) for t in nots)


def test_two_identical_runs_are_all_equal_with_no_divergence() -> None:
    specs = [tool("read"), ("file.modified", {"file.path": "a.py"}), tool("test", "error")]
    result = compare(side(synth(*specs)), side(synth(*specs)))
    assert {r.relation for r in result.rows} == {"equal"}
    assert result.divergence is None and result.first_outcome_difference is None
    assert result.quality == "full" and result.alignment == "content"


def test_arrival_order_and_a_clock_skewed_by_hours_change_nothing() -> None:
    ls = ("shell.command.completed", {"shell.command": "ls"}, "success")
    specs = [tool("read"), tool("edit"), ls, tool("test")]
    base = compare(side(synth(*specs)), side(synth(*specs)))
    shuffled = compare(
        side(synth(*specs)), side(synth(*specs, shuffle=random.Random(1), skew=timedelta(hours=7)))
    )
    assert [r.relation for r in shuffled.rows] == [r.relation for r in base.rows]
    assert shuffled.divergence is None


def test_renamed_ids_temp_dirs_and_digits_do_not_make_steps_differ() -> None:
    def run(tmp: str, ident: str, port: int) -> SideInput:
        root = f"/tmp/{tmp}/repo"  # noqa: S108 - a string in a test, never opened
        return side(synth(
            ("shell.command.completed",
             {"shell.command": f"cd {root} && pytest --id {ident} --port {port}",
              "shell.cwd": root}, "success"),
            ("file.modified", {"file.path": f"{root}/app/x.py"}),
        ))  # fmt: skip

    result = compare(
        run("abc123", "evt_01M4QNE6M0HEM4JTH54PQ1E812", 8080),
        run("zzz999", "evt_01M4QNE6M0VAGXZ1E5D73PHGV8", 9191),
    )
    assert {r.relation for r in result.rows} == {"equal"}


def test_an_inserted_loop_of_fifty_repeated_steps_is_one_left_only_block() -> None:
    base = [tool("a"), tool("b"), tool("c")]
    looped = [tool("a"), *[tool("poll")] * 50, tool("b"), tool("c")]
    result = compare(side(synth(*base)), side(synth(*looped)))
    relations = [r.relation for r in result.rows]
    assert relations.count("right_only") == 50 and relations.count("equal") == 3
    assert result.divergence == 1 and result.quality == "full"


def test_a_changed_status_is_the_first_outcome_difference_and_not_a_divergence_of_keys() -> None:
    a = side(synth(tool("read"), tool("test", "success"), tool("push")))
    b = side(synth(tool("read"), tool("test", "error"), tool("push")))
    result = compare(a, b)
    assert [r.relation for r in result.rows] == ["equal", "changed", "equal"]
    assert result.divergence == result.first_outcome_difference == 1


def test_truncation_at_the_event_and_step_caps_is_reported() -> None:
    events = synth(*[tool(f"t{n}") for n in range(30)])
    cut = compare(side(events, event_count=500), side(events), steps=10)
    assert cut.left.steps == 10 and cut.left.truncated and cut.right.truncated
    assert cut.left.truncated_at_event == cut.left.truncated_at_event is not None
    whole = compare(side(events), side(events))
    assert not whole.left.truncated and whole.left.truncated_at_event is None
    stored_more = compare(side(events, event_count=100), side(events))
    assert stored_more.left.truncated and not stored_more.right.truncated


def test_a_metadata_actor_cannot_tell_that_two_commands_differ() -> None:
    def run(command: str, path: str) -> SideInput:
        return side(synth(
            ("shell.command.completed", {"shell.command": command}, "success"),
            ("file.modified", {"file.path": path}),
            tool("edit"),
        ))  # fmt: skip

    a, b = run("curl -H 'Bearer s3cr3t' x", "app/a.py"), run("ls -la", "other/b.py")
    metadata = compare(a, b, content=False)
    assert {r.relation for r in metadata.rows} == {"equal"} and metadata.divergence is None
    assert metadata.alignment == "metadata" and metadata.files.withheld
    assert (metadata.files.only_left, metadata.files.both) == ((), ())
    assert all(r.left and r.left.label is None for r in metadata.rows[:2])
    assert compare(a, b, content=True).divergence is not None  # content actors do see it


def test_property_metadata_output_never_contains_content() -> None:
    """AC-5: random commands, paths and span names never reach a metadata projection."""
    rng = random.Random(9)
    secrets = [f"SECRET{rng.randrange(10**9)}" for _ in range(40)]
    specs = []
    for secret in secrets:
        specs += [
            ("shell.command.completed", {"shell.command": f"run {secret}"}, "success"),
            ("file.modified", {"file.path": f"/home/{secret}/x.py"}),
            ("git.commit", {"git.branch": secret}),
            ("span.completed", {"span.name": secret}),
        ]
    events = synth(*specs)
    result = compare(side(events), side(synth(*specs[:80])), content=False)
    blob = repr(result)
    assert not any(secret in blob for secret in secrets)


# ------------------------------------------------------------------ the demo pair


def load_pair(*, same_file_edit: bool = False) -> tuple[list[Event], list[Event], dict[str, Any]]:
    if not same_file_edit:
        wires = [
            [json.loads(line) for line in (FIXTURE / name).read_text().splitlines()]
            for name in ("baseline.ndjson", "wrong_fix.ndjson")
        ]
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
    else:
        spec = importlib.util.spec_from_file_location(
            "gen", REPO / "scripts" / "gen_compare_fixture.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["gen"] = module
        spec.loader.exec_module(module)
        wires = [module.build("baseline")[0], module.build("wrong-fix", same_file_edit=True)[0]]
        manifest = {
            "baseline": module.build("baseline")[1],
            "wrong_fix": module.build("wrong-fix")[1],
        }
    return [to_event(w) for w in wires[0]], [to_event(w) for w in wires[1]], manifest


def demo(content: bool, *, same_file_edit: bool = False) -> tuple[Comparison, dict[str, Any]]:
    baseline, wrong, manifest = load_pair(same_file_edit=same_file_edit)
    left = side(baseline, status="SUCCESS", files_modified=2)
    right = side(wrong, status="FAILED", files_modified=2)
    return compare(left, right, content=content), manifest


def test_the_fixture_is_current() -> None:
    import subprocess

    done = subprocess.run(  # noqa: S603
        [sys.executable, str(REPO / "scripts" / "gen_compare_fixture.py"), "--check", str(FIXTURE)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


def test_content_projection_diverges_at_the_second_fix_and_its_outcome_at_the_second_test_run() -> (
    None
):
    result, manifest = demo(content=True)
    assert result.quality == "full" and result.alignment == "content"
    assert result.divergence is not None and result.first_outcome_difference is not None
    first = result.rows[result.divergence]
    # The second edit starts with the file read (the recorder reads before it writes): the wrong
    # fix opens app/oauth.py where the baseline opens app/session.py.
    assert first.relation == "changed" and first.left and first.right
    assert first.left.event_id == manifest["baseline"]["second_edit_read"]
    assert first.right.event_id == manifest["wrong_fix"]["second_edit_read"]
    after = result.rows[result.divergence + 1]
    assert after.relation == "changed" and after.right is not None
    assert after.left and after.left.event_id == manifest["baseline"]["second_edit_modified"]
    assert after.right.event_id == manifest["wrong_fix"]["second_edit_modified"]
    outcome = result.rows[result.first_outcome_difference]
    assert outcome.left and outcome.right
    assert outcome.left.event_id == manifest["baseline"]["second_test_run"]
    assert outcome.right.event_id == manifest["wrong_fix"]["second_test_run"]
    assert (outcome.left.status, outcome.right.status) == ("success", "error")
    assert result.first_outcome_difference > result.divergence
    tail = [r for r in result.rows if r.relation == "left_only"]
    assert tail and all(r.left and r.left.family in ("git", "tool", "llm") for r in tail)
    assert any(
        r.left and r.left.event_type == "git.push" for r in tail
    )  # the wrong fix never pushed


def test_metadata_projection_diverges_at_the_second_test_run() -> None:
    result, manifest = demo(content=False)
    assert result.alignment == "metadata"
    assert result.divergence is not None
    assert result.divergence == result.first_outcome_difference
    row = result.rows[result.divergence]
    assert row.relation == "changed" and row.left and row.right
    assert row.left.event_id == manifest["baseline"]["second_test_run"]
    assert row.right.event_id == manifest["wrong_fix"]["second_test_run"]
    assert all(r.left.label is None for r in result.rows if r.left and r.left.family == "shell")
    assert result.files.withheld and result.files.left_count == 2


def test_the_edit_of_the_same_file_moves_the_content_divergence_to_the_test_run() -> None:
    """AC-6 mutation: a wrong fix that edits the same file diverges only at the test run."""
    result, manifest = demo(content=True, same_file_edit=True)
    assert result.divergence == result.first_outcome_difference and result.divergence is not None
    assert result.divergence is not None
    row = result.rows[result.divergence]
    assert row.left and row.left.event_id == manifest["baseline"]["second_test_run"]


def test_the_demo_pair_is_symmetric_and_deterministic() -> None:
    baseline, wrong, _ = load_pair()
    forward = compare(side(baseline), side(wrong))
    backward = compare(side(wrong), side(baseline))
    assert [(r.left, r.right) for r in forward.rows] == [(r.right, r.left) for r in backward.rows]
    assert forward.divergence == backward.divergence
    assert compare(side(baseline), side(wrong)) == forward


def test_the_step_extraction_is_content_free_for_metadata_keys() -> None:
    baseline, _, _ = load_pair()
    steps, _ = extract_steps(baseline, started_at=STARTED, content=False, limit=1_000)
    flat = json.dumps([(s.key, s.label) for s in steps])
    for needle in ("unittest", "app/", "session", "tail -n", "abb-"):
        assert needle not in flat
