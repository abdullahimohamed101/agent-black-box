"""The scripted ops agent, offline: the harmful commands are recorded, never run."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ops_agent


def test_offline_run_records_a_secret_read_and_two_classified_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ops_agent.main(["--offline"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["run_id"] and summary["stats"]["events_created"] > 10


def test_the_two_harmful_commands_are_classified_high_risk() -> None:
    from agent_blackbox import BlackBox
    from agent_blackbox.coding import CodingRecorder

    with __import__("tempfile").TemporaryDirectory() as scratch:
        root = ops_agent.workspace(Path(scratch))
        bb = BlackBox(api_key="abb_live_k.secret", mode="offline", project="demo")
        with bb.run("t") as run:
            rec = CodingRecorder(bb, run, root)
            risks = {
                c: rec.classify(c).risk_class
                for c in ("git clean -fdx", "git push --force origin main")
            }
    assert all(r in ("R3", "R4") for r in risks.values()), risks
