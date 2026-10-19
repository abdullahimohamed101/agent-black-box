#!/usr/bin/env python3
"""Run the coding-agent demo and record it with Agent BlackBox.

    uv run --project packages/sdk-python python examples/coding-agent/run_demo.py            # scripted, deterministic
    ANTHROPIC_API_KEY=... uv run --project packages/sdk-python python examples/coding-agent/run_demo.py \\
        --live --i-understand-this-runs-commands    # real model (same as --model anthropic); runs arbitrary
                                                    # shell as you: see README. Not run by the project (KI-044)

`--policy-demo` runs the policy scenario instead (Phase 14): the agent tries to read `.env` (blocked) and to
push to main (paused until a person approves). It needs a server, a published policy (see
policies/starter.yaml) and a key with `policy:check` or `events:write`; it asks the policy service before every
file, shell and git action. See README.md.

Needs BLACKBOX_API_KEY (a project key with events:write and artifacts:write) and BLACKBOX_ENDPOINT. The demo
turns on full payload capture (diffs and terminal output are redacted, then uploaded as artifacts); that is a
deliberate opt-in, see docs/SECURITY.md and ADR-031. Prints one JSON line with the run id.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))  # demo_clock.py

from blackbox import BlackBox, PayloadMode
from blackbox.coding import CodingRecorder
from blackbox.policy import ApprovalPending
from demo_clock import DemoClock, LatentModel, hours

from coding_agent.agent import AgentResult, run_agent
from coding_agent.models import ScriptedModel
from coding_agent.workspace import prepare_workspace


def plant_env_file(root: Path) -> None:
    """A placeholder `.env` for the agent to try to read. Not a secret, and kept out of commits."""
    (root / ".env").write_text("# demo placeholder, not a secret\nDEMO_MODE=1\n")
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as handle:
        handle.write(".env\n")


def announce(pending: ApprovalPending) -> None:
    """Called by the SDK when a person has to approve: print the id so a human or a script can."""
    print(
        json.dumps(
            {
                "event": "approval_pending",
                "approval_id": pending.approval_id,
                "policy_id": pending.policy_id,
                "policy_version": pending.policy_version,
                "expires_in_seconds": round(pending.expires_in),
            }
        ),
        flush=True,
    )
    print(
        f"Waiting for approval {pending.approval_id} (policy {pending.policy_id}). "
        "Approve it on the Approvals page, or with the API.",
        file=sys.stderr,
        flush=True,
    )


class AgentFailed(Exception):
    """Raised inside the run so the SDK records the run (and its agent span) as failed."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", choices=["scripted", "anthropic"], default="scripted")
    parser.add_argument(
        "--live",
        action="store_true",
        help="use a real model (same as --model anthropic); needs --i-understand-this-runs-commands "
        "and ANTHROPIC_API_KEY. Not run by the project (KI-044)",
    )
    parser.add_argument(
        "--variant",
        choices=["baseline", "wrong-fix"],
        default="baseline",
        help="scripted model only: `wrong-fix` never applies the real second patch, the tests keep "
        "failing and the run ends in error (the comparison and experiment demos use both)",
    )
    parser.add_argument(
        "--policy-demo",
        action="store_true",
        help="the policy scenario: a blocked read of .env, then a push to main that waits for approval "
        "(needs a server and a published policy; scripted model only)",
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="with --policy-demo: give up waiting for the approval after this long (default: the "
        "approval's own expiry)",
    )
    parser.add_argument(
        "--name",
        default="Fix OAuth session expiry",
        help="the run's name in the runs list (`make demo` gives each of its runs its own)",
    )
    parser.add_argument(
        "--started-ago",
        type=float,
        default=None,
        metavar="HOURS",
        help="scripted model only: record the run as started this many hours ago, with believable model "
        "latencies (virtual time, nothing waits; see examples/python/demo_clock.py)",
    )
    parser.add_argument(
        "--workdir", type=Path, help="where to build the working copy (default: a temp dir)"
    )
    parser.add_argument(
        "--offline", action="store_true", help="record nothing remotely (events stay in memory)"
    )
    parser.add_argument(
        "--i-understand-this-runs-commands",
        action="store_true",
        help="REQUIRED for --model anthropic: a real model runs arbitrary shell commands as you, unsandboxed",
    )
    args = parser.parse_args(argv)
    if args.live:
        args.model = "anthropic"
    if args.policy_demo and (
        args.model != "scripted" or args.variant != "baseline" or args.offline
    ):
        parser.error("--policy-demo is the scripted scenario and needs a server (not --offline)")
    if args.approval_timeout is not None and not args.policy_demo:
        parser.error("--approval-timeout only applies to --policy-demo")
    if args.started_ago is not None and (args.model != "scripted" or args.policy_demo):
        parser.error("--started-ago is for the scripted model and not for --policy-demo")
    if args.model == "anthropic" and args.variant != "baseline":
        parser.error("--variant only applies to the scripted model")
    if args.model == "anthropic" and not args.i_understand_this_runs_commands:
        parser.error(
            "real-model mode lets a language model run arbitrary shell commands as your user, with no "
            "sandbox (recorded commands get a minimal environment, but your files and network are open). "
            "Run it in a disposable VM or container, then pass --i-understand-this-runs-commands."
        )

    # `python` in recorded commands must be the interpreter running the demo.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    if args.model == "anthropic":
        from coding_agent.anthropic_model import AnthropicModel

        model = AnthropicModel()
    else:
        model = ScriptedModel.for_variant("policy" if args.policy_demo else args.variant)
        if args.started_ago is not None:
            model = LatentModel(
                model, DemoClock(hours(args.started_ago)), lambda r: r.output_tokens
            )

    with tempfile.TemporaryDirectory(prefix="abb-coding-demo-") as scratch:
        workdir = args.workdir or Path(scratch)
        root = prepare_workspace(workdir)
        policy_options: dict[str, object] = {}
        if args.policy_demo:
            plant_env_file(root)
            policy_options = {"policy_mode": "enforce", "policy_on_approval": announce}
            if args.approval_timeout is not None:
                policy_options["policy_wait_max"] = args.approval_timeout
        bb = BlackBox(
            project="coding-agent-demo",
            agent_id="coding-agent",
            mode="offline" if args.offline else "http",
            payload_mode=PayloadMode.FULL,
            **policy_options,
        )
        result: AgentResult | None = None
        try:
            with bb.run(args.name, metadata={"model": model.name}) as run:
                run_id = run.run_id
                rec = CodingRecorder(bb, run, root)
                result = run_agent(bb, run, rec, model)
                if not result.tests_passed and not args.policy_demo:
                    # The run did not do its job: it ends in error (run.failed), not as a success.
                    raise AgentFailed("the tests still fail")
        except AgentFailed:
            pass
        assert result is not None
        flushed = bb.shutdown(15)
        summary: dict[str, object] = {
            "run_id": run_id,
            "tests_passed": result.tests_passed,
            "retries": result.retries,
            "turns": result.turns,
            "flushed": flushed,
            "stats": bb.stats(),
        }
        if args.policy_demo:
            summary["blocked"] = list(result.blocked)
            summary["pushed"] = result.pushed
        print(json.dumps(summary))
    if args.policy_demo:
        return (
            0  # the scenario ran to its end: what policy blocked is in the summary, not the status
        )
    return 0 if result.tests_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
