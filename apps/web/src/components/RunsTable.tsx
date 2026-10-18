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
import { runs as t } from "@/copy/runs";
import { runPath, type Base } from "@/lib/routes";
import { matchedLabels, matchedOf } from "@/lib/search";
import { findingsCell } from "@/lib/findings";
import { failureLine } from "@/lib/runsListRow";
import { ShieldGlyph } from "./SecuritySeverity";
import { StatusBadge } from "./StatusBadge";
import { TraceMarker } from "./TraceNotice";

function FindingsCell({ badge }: { badge: RunOut["findings"] }) {
  const c = findingsCell(badge);
  return (
    <td className="c-find" data-testid="findings-cell" data-state={c.state} title={c.title}>
      {c.visible ? (
        // Severity is carried by weight, never by colour (ADR-131): critical is bold, the rest regular.
        <span className="finding-count" data-tone={c.tone}>
          <ShieldGlyph filled={c.tone === "bad"} /> {c.visible}
        </span>
      ) : (
        <span aria-hidden="true">—</span>
      )}
      <span className="sr-only">{c.title}</span>
    </td>
  );
}

/**
 * The runs table. A row is one line (status, name, agent, started, duration, cost, findings); a run that needs attention
 * gets a second line with the miniature red dash, the only red besides the status word. Below 900 px the agent and
 * duration move under the name; below 600 px each row is a block (CSS only, the markup is the same).
 */
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
              <th scope="col" className="c-pick">
                <span className="sr-only">{t.table.pick}</span>
              </th>
            )}
            <th scope="col" className="c-status">
              {t.table.status}
            </th>
            <th scope="col" className="c-run">
              {t.table.run}
            </th>
            <th scope="col" className="c-agent">
              {t.table.agent}
            </th>
            <th scope="col" className="c-started">
              {t.table.started}
            </th>
            <th scope="col" className="num c-dur">
              {t.table.duration}
            </th>
            <th scope="col" className="num c-cost">
              {t.table.cost}
            </th>
            {showMatched && (
              <th scope="col" className="c-match">
                {t.table.matched}
              </th>
            )}
            <th scope="col" className="c-find">
              {t.table.findings}
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const retries = num(r.summary["retry_count"]);
            const fail = failureLine(r);
            const expanded = open.has(r.id);
            const detailId = `${uid}-${r.id}`;
            const agent = r.agent_id ?? "—";
            return (
              <Fragment key={r.id}>
                <tr
                  data-picked={picks.includes(r.id) ? "true" : undefined}
                  data-attention={fail ? "true" : undefined}
                >
                  {pickable && (
                    <td className="compare-pick c-pick">
                      <input
                        type="checkbox"
                        aria-label={`Compare ${r.name ?? r.id}`}
                        checked={picks.includes(r.id)}
                        disabled={!picks.includes(r.id) && picks.length >= COMPARE_PICKS}
                        onChange={() => onToggle(r.id)}
                      />
                    </td>
                  )}
                  <td className="c-status">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="name c-run">
                    <div className="run-line">
                      <Link href={runPath(base, r.id)} data-failed={fail ? "true" : undefined}>
                        {r.name ?? r.id}
                      </Link>
                      {retries && !fail ? (
                        <span className="tag">{t.table.retries(retries)}</span>
                      ) : null}
                      <button
                        type="button"
                        className="row-more"
                        aria-expanded={expanded}
                        aria-controls={detailId}
                        aria-label={t.table.details(expanded, r.name ?? r.id)}
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
                        <span className="run-fail-dash" aria-hidden="true" />
                        <span className="run-fail-sentence">{fail.sentence}</span>
                        <span className="run-fail-detail">
                          {fail.detail && ` · ${fail.detail}`}
                        </span>
                      </p>
                    )}
                    <p className="run-meta">
                      {agent} · {formatDuration(r.duration_ms)}
                    </p>
                  </td>
                  <td className="c-agent">{agent}</td>
                  <td className="c-started" title={formatTimestamp(r.started_at)}>
                    {formatRelative(r.started_at, now)}
                  </td>
                  <td className="num c-dur">{formatDuration(r.duration_ms)}</td>
                  <td className="num c-cost">{formatCost(num(r.summary["estimated_cost_usd"]))}</td>
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
    <td className="c-match" data-testid="matched">
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
