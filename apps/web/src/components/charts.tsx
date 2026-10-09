import { DASH } from "@/lib/format";

/**
 * Small charts built from plain elements. Every label and value is a React text node (never HTML), because
 * agent, model and tool names are client-controlled telemetry. Each chart has its figures in text, so it is
 * readable without seeing the bars.
 */
export type Bar = { label: string; value: number; display: string; note?: string };

export function BarList({ bars, caption }: { bars: readonly Bar[]; caption: string }) {
  const max = Math.max(...bars.map((b) => b.value), 0);
  if (bars.length === 0) return <p className="muted">No data in this window.</p>;
  return (
    <ul className="bars" aria-label={caption}>
      {bars.map((b) => (
        <li key={b.label}>
          <span className="bar-label" title={b.label}>
            {b.label}
          </span>
          <span className="bar-track" aria-hidden="true">
            <span className="bar-fill" style={{ width: `${max ? (b.value / max) * 100 : 0}%` }} />
          </span>
          <span className="bar-value">
            {b.display}
            {b.note && <small className="muted"> {b.note}</small>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export type Column = { label: string; value: number; display: string; tone?: "ok" | "bad" };

/** Vertical columns per day, with the numbers in a table below for screen readers and exact reading. */
export function DayColumns({
  days,
  caption,
  valueHeader,
}: {
  days: readonly Column[];
  caption: string;
  valueHeader: string;
}) {
  if (days.length === 0) return <p className="muted">No data in this window.</p>;
  const max = Math.max(...days.map((d) => d.value), 0);
  return (
    <figure className="columns">
      <div className="column-row" aria-hidden="true">
        {days.map((d) => (
          <div key={d.label} className="column" title={`${d.label}: ${d.display}`}>
            <span
              className="column-fill"
              data-tone={d.tone}
              style={{
                height: `${max ? Math.max((d.value / max) * 100, d.value > 0 ? 3 : 0) : 0}%`,
              }}
            />
          </div>
        ))}
      </div>
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">{valueHeader}</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.label}>
              <th scope="row">{d.label}</th>
              <td>{d.display || DASH}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <figcaption className="muted">
        {caption}: {days[0]!.label} to {days[days.length - 1]!.label}
      </figcaption>
    </figure>
  );
}
