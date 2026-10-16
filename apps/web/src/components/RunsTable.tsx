import Link from "next/link";
import { Fragment, useId, useState } from "react";
import "../app/runs-list.css";
import type { RunOut } from "@/lib/api/types";
import {
  formatCost,
  formatDuration,
  formatInt,
  formatRelative,
  formatTimestamp,
  num,
} from "@/lib/format";
import { COMPARE_PICKS } from "@/lib/compare";
import { runPath, type Base } from "@/lib/routes";
import { matchedLabels, matchedOf } from "@/lib/search";
import { findingsCell } from "@/lib/findings";
import { failureLine } from "@/lib/runsListRow";
import { StatusBadge } from "./StatusBadge";
import { TraceMarker } from "./TraceNotice";

function FindingsCell({ badge }: { badge: RunOut["findings"] }) {
  const c = findingsCell(badge);
  return (
    <td data-testid="findings-cell" data-state={c.state} title={c.title}>
      {c.visible ? (
        <span className="badge" data-tone={c.tone}>
          <span aria-hidden="true">{c.glyph}</span> {c.visible}
        </span>
      ) : (
        <span aria-hidden="true">—</span>
      )}
      <span className="sr-only">{c.title}</span>
    </td>
  );
}

export function RunsTable({
  runs,
  base,
  caption,
  now,
  selected,
  onToggle,
}: {
  runs: readonly RunOut[];
  base: Base;
  caption: string;
  now?: number;
  /** Compare picks (run ids). The checkbox column exists only when `onToggle` is given (the list is comparing). */
  selected?: readonly string[];
  onToggle?: (runId: string) => void;
}) {
  const pickable = onToggle !== undefined;
  const picks = selected ?? [];
  const uid = useId();
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const flip = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  // The column exists only for a search that named indexed fields or words; the API sends `matched` for those.
  const showMatched = runs.some((r) => matchedOf(r) !== null);
  const cols = 7 + (pickable ? 1 : 0) + (showMatched ? 1 : 0);
  return (
    <div className="table-wrap">
      <table className="runs-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {pickable && (
              <th scope="col">
                <span className="sr-only">Pick to compare</span>
              </th>
            )}
            <th scope="col">Status</th>
            <th scope="col">Run</th>
            <th scope="col">Agent</th>
            <th scope="col">Started</th>
            <th scope="col" className="num">
              Duration
            </th>
            <th scope="col" className="num">
              Cost
            </th>
            {showMatched && <th scope="col">Matched</th>}
            <th scope="col">Findings</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const retries = num(r.summary["retry_count"]);
            const fail = failureLine(r);
            const expanded = open.has(r.id);
            const detailId = `${uid}-${r.id}`;
            return (
              <Fragment key={r.id}>
                <tr
                  data-picked={picks.includes(r.id) ? "true" : undefined}
                  data-attention={fail ? "true" : undefined}
                >
                  {pickable && (
                    <td className="compare-pick">
                      <input
                        type="checkbox"
                        aria-label={`Compare ${r.name ?? r.id}`}
                        checked={picks.includes(r.id)}
                        disabled={!picks.includes(r.id) && picks.length >= COMPARE_PICKS}
                        onChange={() => onToggle(r.id)}
                      />
                    </td>
                  )}
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="name">
                    <div className="run-line">
                      <Link href={runPath(base, r.id)}>{r.name ?? r.id}</Link>
                      {retries && !fail ? (
                        <span className="tag">
                          {retries} retr{retries === 1 ? "y" : "ies"}
                        </span>
                      ) : null}
                      <button
                        type="button"
                        className="row-more"
                        aria-expanded={expanded}
                        aria-controls={detailId}
                        aria-label={`${expanded ? "Hide" : "Show"} details of ${r.name ?? r.id}`}
                        onClick={() => flip(r.id)}
                      >
                        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
                      </button>
                    </div>
                    {r.trace_state !== "available" && (
                      <p className="run-trace">
                        <TraceMarker state={r.trace_state} />
                      </p>
                    )}
                    {fail && (
                      <p className="run-fail" data-testid="failure-line">
                        <span className="run-fail-sentence">{fail.sentence}</span>
                        <span className="run-fail-detail">
                          {fail.detail && ` · ${fail.detail}`}
                        </span>
                      </p>
                    )}
                  </td>
                  <td>{r.agent_id ?? "—"}</td>
                  <td title={formatTimestamp(r.started_at)}>{formatRelative(r.started_at, now)}</td>
                  <td className="num">{formatDuration(r.duration_ms)}</td>
                  <td className="num">{formatCost(num(r.summary["estimated_cost_usd"]))}</td>
                  {showMatched && <MatchedCell run={r} />}
                  <FindingsCell badge={r.findings} />
                </tr>
                {expanded && (
                  <tr id={detailId} className="row-detail" data-testid="row-detail">
                    <td colSpan={cols}>
                      <RowDetail run={r} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The counts the table no longer carries as columns. Plain figures from the run summary, nothing captured. */
function RowDetail({ run }: { run: RunOut }) {
  const s = run.summary;
  const items: [string, string][] = [
    ["Events", formatInt(num(s["event_count"]))],
    ["Errors", formatInt(num(s["error_count"]) ?? 0)],
    ["Retries", formatInt(num(s["retry_count"]) ?? 0)],
    ["Model calls", formatInt(num(s["llm_calls"]))],
    ["Tool calls", formatInt(num(s["tool_calls"]))],
    [
      "Tokens in / out",
      `${formatInt(num(s["input_tokens"]))} / ${formatInt(num(s["output_tokens"]))}`,
    ],
  ];
  return (
    <dl className="row-facts">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function MatchedCell({ run }: { run: RunOut }) {
  const m = matchedOf(run);
  const labels = m ? matchedLabels(m) : [];
  const truncated = !!m && (m.metadata_truncated === true || m.content_truncated === true);
  return (
    <td data-testid="matched">
      <span className="matched">
        {labels.map((l) => (
          <span key={l} className="tag">
            {l}
          </span>
        ))}
        {truncated && (
          <span
            className="tag tag-warn"
            title="This run has more values than the search index holds, so some may not be searchable."
          >
            index truncated
          </span>
        )}
        {labels.length === 0 && !truncated && "—"}
      </span>
    </td>
  );
}
