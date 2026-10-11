"""The offline event fixture of the two demo runs the comparison tests align (Phase 11, AC-6).

The events are produced by the REAL coding-agent example (`examples/coding-agent`, scripted model, offline
recorder): `baseline` fixes the bug (the second edit is `app/session.py`, the tests pass, the run commits
and pushes); `wrong-fix` makes the same first moves, then edits `app/oauth.py` instead, the tests still fail
and the run ends in error (`run.failed`). The raw run is made deterministic here: ids are derived from a
fixed seed, the clock is synthetic (the wrong-fix run starts later), durations and git hashes are fixed and
the SDK stamp is dropped. Nothing else is rewritten, so the shapes are the recorder's own.

    python3 scripts/gen_compare_fixture.py --out apps/api/tests/fixtures/compare_pair
    python3 scripts/gen_compare_fixture.py --check apps/api/tests/fixtures/compare_pair

Standard library only (the SDK and the example are put on `sys.path`); runs git and python as the demo does.
`manifest.json` names the events the tests assert on, by id; it is written here, never by hand.
`same_file_edit=True` (the mutation of AC-6) makes the wrong-fix edit the same file as the baseline.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "sdk-python" / "src"))
sys.path.insert(0, str(REPO / "examples" / "coding-agent"))

FIXED_MS = int(datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)
STEP_SECONDS = 0.4
FIXED_DURATION_MS = 250.0
_ALPHABET = (
    "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (the event contract's ULID alphabet)
)
Event = dict[str, Any]


def _ulid(seed: str) -> str:
    value = (FIXED_MS << 80) | int.from_bytes(hashlib.sha256(seed.encode()).digest()[:10], "big")
    return "".join(_ALPHABET[(value >> (5 * i)) & 0x1F] for i in reversed(range(26)))


def _run_example(variant: str, same_file_edit: bool) -> list[Event]:
    """The raw events of one scripted run of the example, in sequence order."""
    from blackbox import BlackBox, PayloadMode
    from blackbox.coding import CodingRecorder
    from coding_agent.agent import run_agent
    from coding_agent.models import ScriptedModel
    from coding_agent.workspace import prepare_workspace

    model = ScriptedModel.for_variant(variant)
    if same_file_edit:  # a wrong fix that edits the same file as the real one (a docstring)
        call = model.turns[5].tool_calls[0]
        args = {"path": "app/session.py", "old": '"""Session expiry and refresh."""',
                "new": '"""Session expiry and refresh (reviewed)."""'}  # fmt: skip
        model.turns[5] = type(model.turns[5])(
            model.turns[5].text, (type(call)(call.id, call.name, args),),
            model.turns[5].input_tokens, model.turns[5].output_tokens, model.turns[5].cost_usd,
        )  # fmt: skip
    previous_path = os.environ["PATH"]
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + previous_path
    try:
        with tempfile.TemporaryDirectory(prefix="abb-compare-fixture-") as scratch:
            root = prepare_workspace(Path(scratch))
            bb = BlackBox(project="coding-agent-demo", agent_id="coding-agent", mode="offline",
                          payload_mode=PayloadMode.FULL)  # fmt: skip
            try:
                with bb.run("Fix OAuth session expiry", metadata={"model": model.name}) as run:
                    result = run_agent(bb, run, CodingRecorder(bb, run, root), model)
                    if not result.tests_passed:
                        raise RuntimeError("the tests still fail")  # as run_demo.py does
            except RuntimeError:
                pass
            return sorted(bb.buffered_events(), key=lambda e: e["sequence"])
    finally:
        os.environ["PATH"] = previous_path


def _normalise(variant: str, raw: list[Event]) -> list[Event]:
    ids: dict[str, str] = {}

    def new(old: str | None) -> str | None:
        if old is None:
            return None
        if old not in ids:
            ids[old] = f"{old.split('_', 1)[0]}_{_ulid(f'{variant}:{len(ids)}')}"
        return ids[old]

    start = datetime(2026, 10, 12, 9, 0, 0, tzinfo=UTC)
    if variant != "baseline":
        start += timedelta(hours=2, minutes=17)  # a different clock for the second run
    events: list[Event] = []
    for event in raw:
        out = {k: v for k, v in event.items() if k != "sdk"}
        for key in ("event_id", "run_id", "trace_id", "span_id", "parent_span_id"):
            if key in out:
                out[key] = new(out[key])
        when = start + timedelta(seconds=STEP_SECONDS * event["sequence"])
        out["occurred_at"] = when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if "duration_ms" in out:
            out["duration_ms"] = FIXED_DURATION_MS
        attrs = dict(out["attributes"])
        for key, value in attrs.items():
            if key.endswith("_ms") and isinstance(value, (int, float)):
                attrs[key] = FIXED_DURATION_MS
            elif key in ("git.commit_hash", "git.head_commit", "git.base_commit"):
                attrs[key] = hashlib.sha1(f"{variant}:{key}".encode()).hexdigest()  # noqa: S324
        out["attributes"] = attrs
        events.append(out)
    return events


def _names(events: list[Event]) -> dict[str, str]:
    """The ids the tests assert on, found by shape (not by position): the test runs and the second edit."""
    tests = [e for e in events if e["event_type"].startswith("shell.command.")
             and e["event_type"] != "shell.command.started" and "test.framework" in e["attributes"]]  # fmt: skip
    modified = [i for i, e in enumerate(events) if e["event_type"] == "file.modified"]
    second = modified[1]
    assert events[second - 1]["event_type"] == "file.read", "the recorder reads before it writes"
    return {
        "first_test_run": tests[0]["event_id"],
        "second_test_run": tests[1]["event_id"],
        "second_edit_read": events[second - 1]["event_id"],
        "second_edit_modified": events[second]["event_id"],
    }


def build(variant: str, *, same_file_edit: bool = False) -> tuple[list[Event], dict[str, str]]:
    """The wire events and the named event ids of one run: variant `baseline` or `wrong-fix`."""
    events = _normalise(variant, _run_example(variant, same_file_edit and variant != "baseline"))
    return events, _names(events)


def render() -> dict[str, str]:
    """File name -> text of the whole fixture."""
    files: dict[str, str] = {}
    manifest: dict[str, Any] = {}
    for variant, key in (("baseline", "baseline"), ("wrong-fix", "wrong_fix")):
        events, names = build(variant)
        files[f"{key}.ndjson"] = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
        manifest[key] = {"run_id": events[0]["run_id"], "events": len(events), **names}
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="write the fixture files here")
    parser.add_argument("--check", type=Path, help="exit 1 if the files here differ")
    args = parser.parse_args()
    files = render()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (args.out / name).write_text(text)
    if args.check:
        stale = [n for n, t in files.items() if (args.check / n).read_text() != t]
        if stale:
            print("stale fixture files:", ", ".join(stale), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
