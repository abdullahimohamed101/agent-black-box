import type { ReactNode } from "react";

/** Section 2 (#why): the trace's two halves in the product's own terms. No rule here: the page has one fault line, in the hero. */
function Half({ title, text, rows }: { title: string; text: string; rows: { type: ReactNode; note: string }[] }) {
  return (
    <div>
      <h3 className="h3">{title}</h3>
      <p className="half-text">{text}</p>
      <ul className="mono-list">
        {rows.map((r, i) => (
          <li key={i}>
            <span>{r.type}</span>
            <span className="q">{r.note}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function BeforeAfter() {
  return (
    <section className="band" id="why" aria-labelledby="why-h">
      <div className="wrap">
        <p className="eyebrow">What a run holds</p>
        <h2 className="h2" id="why-h">It records what happened, and what the failure cost.</h2>
        <div className="halves">
          <Half
            title="Before the line: what the agent did"
            text="Model calls with tokens and cost, tool calls with their name and duration, commands, file edits. In order, each with the time it happened."
            rows={[
              { type: "llm.request.completed", note: "2,300 in · $0.0189" },
              { type: "file.read", note: "0042_billing.sql" },
              { type: "tool.call.started", note: "run_migration" },
            ]}
          />
          <Half
            title="After the line: what it cost you"
            text="Two retries and $0.0167 spent after the first error, counted for you, and a finding that names the cause and links the events it was built from."
            rows={[
              { type: <span className="fail">tool.call.failed</span>, note: "ConnectionTimeout" },
              { type: <span className="caution">retry.attempted</span>, note: "#1, #2" },
              { type: <span className="fail">run.failed</span>, note: "+14.4 s · $0.0356 total" },
            ]}
          />
        </div>
      </div>
    </section>
  );
}
