import Link from "next/link";
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
import { StatusBadge } from "./StatusBadge";

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
  /** Compare picks (run ids). The checkbox column exists only when `onToggle` is given. */
  selected?: readonly string[];
  onToggle?: (runId: string) => void;
}) {
  const pickable = onToggle !== undefined;
  const picks = selected ?? [];
  // The column exists only for a search that named indexed fields or words; the API sends `matched` for those.
  const showMatched = runs.some((r) => matchedOf(r) !== null);
  return (
    <div className="table-wrap">
      <table>
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
            <th scope="col" className="num">
              Events
            </th>
            <th scope="col" className="num">
              Errors
            </th>
            {showMatched && <th scope="col">Matched</th>}
            <th scope="col">Findings</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const errors = num(r.summary["error_count"]);
            const retries = num(r.summary["retry_count"]);
            return (
              <tr key={r.id} data-picked={picks.includes(r.id) ? "true" : undefined}>
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
                  <Link href={runPath(base, r.id)}>{r.name ?? r.id}</Link>
                  {retries ? (
                    <span className="tag">
                      {retries} retr{retries === 1 ? "y" : "ies"}
                    </span>
                  ) : null}
                </td>
                <td>{r.agent_id ?? "—"}</td>
                <td title={formatTimestamp(r.started_at)}>{formatRelative(r.started_at, now)}</td>
                <td className="num">{formatDuration(r.duration_ms)}</td>
                <td className="num">{formatCost(num(r.summary["estimated_cost_usd"]))}</td>
                <td className="num">{formatInt(num(r.summary["event_count"]))}</td>
                <td className="num">{errors ? <strong>{errors}</strong> : 0}</td>
                {showMatched && <MatchedCell run={r} />}
                <FindingsCell badge={r.findings} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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
