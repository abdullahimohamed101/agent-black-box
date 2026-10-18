import { Fragment } from "react";
import { DASH } from "@/lib/format";
import "../app/charts.css";

/**
 * Small charts built from plain elements. Every label and value is a React text node (never HTML), because
 * agent, model and tool names are client-controlled telemetry. Each chart has its figures in text, so it is
 * readable without seeing the bars.
 */
export type Bar = {
  label: string;
  value: number;
  display: string;
  note?: string;
  /** The mark that leads (an experiment's winner): ink, while the others are the neutral data colour. Said in words elsewhere. */
  lead?: boolean;
};

export function BarList({
  bars,
  caption,
  marks = "ink",
}: {
  bars: readonly Bar[];
  caption: string;
  /** "ink": every mark is ink. "lead": only a bar with `lead` is ink and the rest are neutral. */
  marks?: "ink" | "lead";
}) {
  const max = Math.max(...bars.map((b) => b.value), 0);
  if (bars.length === 0) return <p className="muted">No data in this window.</p>;
  return (
    <ul className="bars" aria-label={caption} data-marks={marks}>
      {bars.map((b) => (
        <li key={b.label} data-lead={b.lead ? "true" : undefined}>
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

export type Column = { label: string; value: number; display: string };

/** Vertical columns per day, with the numbers in a table below for screen readers and exact reading. */
export function DayColumns({
  days,
  caption,
  valueHeader,
  peak,
}: {
  days: readonly Column[];
  caption: string;
  valueHeader: string;
  /** Says the tallest column in words under the chart, so the marks have a visible text equivalent (never colour or height alone). */
  peak?: (display: string, label: string) => string;
}) {
  if (days.length === 0) return <p className="muted">No data in this window.</p>;
  const max = Math.max(...days.map((d) => d.value), 0);
  const few = days.length < 3;
  const top = max > 0 ? days.find((d) => d.value === max) : undefined;
  return (
    <figure className="columns">
      <div className="column-row" aria-hidden="true" data-few={few ? "true" : undefined}>
        {days.map((d) => (
          <Fragment key={d.label}>
            <div
              className="column"
              data-peak={top === d ? "true" : undefined}
              title={`${d.label}: ${d.display}`}
            >
              <span
                className="column-fill"
                style={{
                  height: `${max ? Math.max((d.value / max) * 100, d.value > 0 ? 3 : 0) : 0}%`,
                }}
              />
            </div>
            {/* With one or two days a column alone says little: the value is printed beside it. */}
            {few && <span className="column-value">{d.display || DASH}</span>}
          </Fragment>
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
        {peak && top && <span className="column-peak">. {peak(top.display, top.label)}</span>}
      </figcaption>
    </figure>
  );
}
