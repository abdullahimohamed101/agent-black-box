import { PREREQUISITES, QUICKSTART_LINES, SDK_SNIPPET } from "../content/commands";
import { CommandBlock } from "./CommandBlock";

/** Section 3 (#quickstart): the primary action. Five commands from docs/quickstart.md, then the SDK snippet. */
export function Quickstart() {
  return (
    <section className="band" id="quickstart" aria-labelledby="quickstart-h">
      <div className="wrap quick">
        <div>
          <p className="eyebrow">Quickstart</p>
          <h2 className="h2" id="quickstart-h">
            Run it on your machine
          </h2>
          <p className="lede lede-sm">
            <code className="mono">make demo</code> records twelve scripted runs from four example agents into a project of their own: a coding agent (runs that pass, fail and wait for an
            approval), a support agent with failed tool calls and retries, an ops agent that trips the security findings, and a five-agent run. No API key. The same agents, calls, errors, retries and costs every time.
          </p>
          <p className="req mono">{PREREQUISITES}</p>
        </div>
        <div className="quick-blocks">
          <CommandBlock id="cmd-quickstart" label="shell" lines={QUICKSTART_LINES} />
          <h3 className="h3 trace-own">Then trace your own agent</h3>
          <p className="half-text">A run, then a span around each step. The SDK is standard-library Python and never raises into your agent.</p>
          <CommandBlock id="cmd-sdk" label="agent.py" lines={SDK_SNIPPET} />
        </div>
      </div>
    </section>
  );
}
