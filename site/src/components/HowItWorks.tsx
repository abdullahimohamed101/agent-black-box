import fixture from "../../fixtures/migrate-billing-schema.json";
import { json, python } from "../lib/code";
import { CommandBlock } from "./CommandBlock";

const AGENT = python(`from blackbox import BlackBox

bb = BlackBox(api_key=..., project="billing-agent")
with bb.run("Migrate billing schema") as run:
    with run.span("run_migration", kind="tool"):
        migrate()`);

// The event shown is step 7 of the hero's own fixture, so the page cannot describe an event it does not draw.
const first = fixture.events.find((e) => e.sequence === 7)!;
const EVENT = json(
  JSON.stringify({ event_type: first.event_type, sequence: first.sequence, status: first.status, duration_ms: first.duration_ms, attributes: first.attributes }, null, 2),
);

/** Section 4 (#how): the mechanism in three steps. Sticky heading on wide screens; each step settles in on scroll (CSS only). */
export function HowItWorks({ headline }: { headline: string }) {
  return (
    <section className="band" id="how" aria-labelledby="how-h">
      <div className="wrap how">
        <div className="how-lead">
          <p className="eyebrow">How it works</p>
          <h2 className="h2" id="how-h">Typed events, then one run.</h2>
          <p className="lede lede-sm">Accepted events are immutable; summaries, costs and findings are rebuilt from them.</p>
        </div>
        <ol className="steps">
          <li className="step">
            <span className="n">01</span>
            <div>
              <h3 className="h3">Wrap the agent</h3>
              <p>A run, then a span around each tool call or command. The SDK never raises into your agent, and its buffer and retries are bounded.</p>
              <CommandBlock id="cmd-how-agent" label="agent.py" lines={AGENT} />
            </div>
          </li>
          <li className="step">
            <span className="n">02</span>
            <div>
              <h3 className="h3">Events arrive typed</h3>
              <p>A failed tool call is a <code className="mono">tool.call.failed</code> event with a name, a duration and an error type, not a string to grep.</p>
              <CommandBlock id="cmd-how-event" label="event" lines={EVENT} />
            </div>
          </li>
          <li className="step">
            <span className="n">03</span>
            <div>
              <h3 className="h3">Read the run from the top</h3>
              <p>The headline sentence says what failed and when. The fault line marks the first error. Findings point at the events that support them.</p>
              <p className="mono quiet headline-sample">{headline}</p>
            </div>
          </li>
        </ol>
      </div>
    </section>
  );
}
