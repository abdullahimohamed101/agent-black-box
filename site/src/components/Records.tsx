const RECORDS: [string, string][] = [
  ["Runs and agents", "run.started, run.failed, agent.spawned: who ran, and how the run ended."],
  ["Model calls", "llm.request.completed with the provider, the model, tokens and cost."],
  ["Tool calls", "tool.call.failed with the tool, its duration and an error type."],
  ["Files and git", "file.read, file.modified, git.commit: what was touched, and file edits as diffs."],
  ["Shell commands", "shell.command.failed with the exit code and a risk class."],
  ["Retries and timeouts", "retry.attempted, timeout.occurred, loop.detected: the steps a log line hides."],
  ["Policy and approvals", "policy.decision, approval.requested, approval.granted: who allowed what, once."],
];

/** Section 7 (#records): scope, plainly, and the integrations with their qualifier. */
export function Records() {
  return (
    <section className="band" id="records" aria-labelledby="records-h">
      <div className="wrap">
        <p className="eyebrow">What it records</p>
        <h2 className="h2" id="records-h">Typed events, not log lines.</h2>
        <p className="lede lede-sm">Captured telemetry is treated as hostile input: it is validated at the boundary and only ever rendered as text.</p>
        <div className="records">
          <dl>
            {RECORDS.map(([term, text]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
          <div>
            <h3 className="h3">Works with</h3>
            <dl className="works">
              <div>
                <dt>Python SDK</dt>
                <dd>Standard library only, with a bounded buffer. If it fails, your agent keeps running.</dd>
              </div>
              <div>
                <dt>Adapters</dt>
                <dd>LangGraph, OpenAI, Anthropic and MCP, tested offline against the real libraries, not against live providers.</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
}
