/** The README's diagram as inline SVG: hairlines and mono labels, no colour of its own. Two drawings, switched by width, so no label is ever under 12 px. */
function Box({ x, y, w, label, sub }: { x: number; y: number; w: number; label: string; sub?: string }) {
  return (
    <g>
      <rect className="dg-box" x={x} y={y} width={w} height={sub ? 56 : 44} rx="4" />
      <text className="dg-t" x={x + w / 2} y={y + (sub ? 24 : 27)} textAnchor="middle">{label}</text>
      {sub ? <text className="dg-s" x={x + w / 2} y={y + 43} textAnchor="middle">{sub}</text> : null}
    </g>
  );
}
const Arrow = ({ d, head }: { d: string; head: string }) => <path className="dg-line" d={d} markerEnd={`url(#${head})`} />;

function Defs({ id }: { id: string }) {
  return (
    <defs>
      <marker id={id} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
        <path className="dg-head" d="M0 0 L8 4 L0 8 z" />
      </marker>
    </defs>
  );
}

export function Architecture() {
  const desc = "The SDK sends events to the API, a modular monolith. The API writes them to PostgreSQL with an outbox, workers derive summaries and findings from them, and the API streams live updates to the web app over server-sent events.";
  return (
    <section className="band" id="architecture" aria-labelledby="arch-h">
      <div className="wrap">
        <p className="eyebrow">Architecture</p>
        <h2 className="h2" id="arch-h">One picture.</h2>
        <figure className="diagram">
          <svg className="dg dg-wide" viewBox="0 0 900 190" role="img" aria-labelledby="dg-title dg-desc">
            <title id="dg-title">Architecture</title>
            <desc id="dg-desc">{desc}</desc>
            <Defs id="dg-head-w" />
            <Box x={0} y={20} w={180} label="Python SDK" />
            <Box x={240} y={14} w={200} label="API" sub="FastAPI, modular monolith" />
            <Box x={500} y={14} w={200} label="PostgreSQL" sub="events and outbox" />
            <Box x={760} y={20} w={140} label="Workers" />
            <Arrow head="dg-head-w" d="M182 42 H236" />
            <Arrow head="dg-head-w" d="M442 42 H496" />
            <Arrow head="dg-head-w" d="M702 42 H756" />
            <Box x={240} y={130} w={200} label="Web app" sub="Next.js" />
            <Arrow head="dg-head-w" d="M340 72 V126" />
            <text className="dg-s" x={350} y={104}>live updates over SSE</text>
          </svg>
          <svg className="dg dg-tall" viewBox="0 0 280 470" role="img" aria-labelledby="dg-title2 dg-desc2">
            <title id="dg-title2">Architecture</title>
            <desc id="dg-desc2">{desc}</desc>
            <Defs id="dg-head-t" />
            <Box x={40} y={0} w={200} label="Python SDK" />
            <Arrow head="dg-head-t" d="M140 46 V84" />
            <Box x={40} y={90} w={200} label="API" sub="FastAPI, modular monolith" />
            <Arrow head="dg-head-t" d="M140 148 V186" />
            <Box x={40} y={192} w={200} label="PostgreSQL" sub="events and outbox" />
            <Arrow head="dg-head-t" d="M140 250 V288" />
            <Box x={40} y={294} w={200} label="Workers" />
            <Box x={40} y={400} w={200} label="Web app" sub="Next.js" />
            <path className="dg-line" d="M240 118 H266 V428 H244" markerEnd="url(#dg-head-t)" />
            <text className="dg-s" x={256} y={330} textAnchor="end" transform="rotate(-90 256 330)">live updates over SSE</text>
          </svg>
          <figcaption>Simple on purpose: no Kafka, no ClickHouse, no Redis until a measured trigger says otherwise.</figcaption>
        </figure>
      </div>
    </section>
  );
}

const FAQ: [string, string][] = [
  [
    "Why not just logs?",
    "Logs are strings you grep after the fact; they do not know a retry from a tool call, cannot price a model call, and nobody writes the first-error line. Agent BlackBox records typed events, derives the headline, cost and findings from them, and links each finding to the events it was built from. If your logs already answer \"which step broke and what did it cost after that\", you do not need this.",
  ],
  [
    "Why not LangSmith or Langfuse?",
    "They are more mature and have evaluation and prompt tooling we do not. Agent BlackBox is narrower: a record you run yourself, with PostgreSQL only, deterministic detectors for loops, cost and risky actions, approvals for actions that should wait for a person, and a demo that needs no key. If you want a hosted service or LangChain-native evaluation workflows, use them.",
  ],
  [
    "Why not OpenTelemetry?",
    "Use it too. Agent BlackBox maps to OpenTelemetry at the boundary and can export its own traces over it; it does not ingest OTLP today. The canonical event contract exists because agent concepts (retry, approval, file edit, policy decision) need to be typed, and the generative-AI semantic conventions are still moving.",
  ],
  [
    "Does it stop a bad action?",
    "Only if the agent asks: guard() and the MCP adapter check the policy before acting and wait for a person when the policy says so. Code that does not go through the guard is recorded, not stopped.",
  ],
];

/** Section 9b (#faq): the four awkward questions, answered plainly. */
export function Faq() {
  return (
    <section className="band" id="faq" aria-labelledby="faq-h">
      <div className="wrap faq-wrap">
        <p className="eyebrow">Questions</p>
        <h2 className="h2" id="faq-h">The awkward ones.</h2>
        <div className="faq">
          {FAQ.map(([q, a]) => (
            <details key={q}>
              <summary>{q}</summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
