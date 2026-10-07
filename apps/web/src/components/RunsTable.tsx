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
import { runPath, type Base } from "@/lib/routes";
import { StatusBadge } from "./StatusBadge";

export function RunsTable({
  runs,
  base,
  caption,
  now,
}: {
  runs: readonly RunOut[];
  base: Base;
  caption: string;
  now?: number;
}) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
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
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const errors = num(r.summary["error_count"]);
            const retries = num(r.summary["retry_count"]);
            return (
              <tr key={r.id}>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>
                  <Link href={runPath(base, r.id)}>{r.name ?? r.id}</Link>
                  {retries ? (
                    <span className="tag">
                      {" "}
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
