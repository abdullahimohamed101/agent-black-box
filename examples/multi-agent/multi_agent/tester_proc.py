"""The tester: a separate OS process that joins the run through the trace context in its environment.

    BLACKBOX_TRACE_CONTEXT=abb1;... python tester_proc.py [--skew-ms N] [--pace X] [--payload-mode full]

`--skew-ms` makes this process's clock run that many milliseconds behind, the way a machine with a bad
clock would. It patches the example's own timestamp function; the SDK has no clock setting.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import blackbox.events
from blackbox import BlackBox, PayloadMode, trace_context_from_env


def skew_clock(skew_ms: float) -> None:
    def behind() -> str:
        moment = datetime.now(timezone.utc) - timedelta(milliseconds=skew_ms)
        return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")

    blackbox.events.now_rfc3339 = behind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skew-ms", type=float, default=0.0)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--payload-mode", default="metadata_only")
    args = parser.parse_args(argv)
    if args.skew_ms:
        skew_clock(args.skew_ms)
    context = trace_context_from_env()
    if context is None:
        print("no valid BLACKBOX_TRACE_CONTEXT in the environment", file=sys.stderr)
        return 2
    # The project comes from the API key; the agent id from the context.
    bb = BlackBox(payload_mode=PayloadMode(args.payload_mode))
    with bb.continue_run(context) as run:  # opens the agent span "tester"
        with run.span(
            "pytest -q", kind="shell", attributes={"shell.command": "pytest -q"}
        ) as shell:
            time.sleep(0.5 * args.pace)
            shell.set_payload({"output": "6 passed in 0.5s"})
    ok = bb.shutdown(10)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
