"use client";

import Link from "next/link";
import { useState } from "react";
import {
  STATUS_FILTERS,
  STATUS_FILTER_WORD,
  TRIGGERS,
  TRIGGER_WORDS,
  TYPE_WORD,
  errorCodeWords,
  notificationStatus,
  skipWords,
  triggerShort,
  type AlertNotification,
} from "@/lib/alerts";
import {
  useAlertDestinations,
  useAlertHistory,
  useAlertRules,
  type HistoryFilters,
} from "@/lib/alertsApi";
import { approvalsPath } from "@/lib/routes";
import { findingHref } from "@/lib/security";
import { StatusMark } from "./AlertMarks";
import { SecuritySeverityMark } from "./SecuritySeverity";
import { when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/alerts.css";

/** What the API's "since/until" are not used for here: the log is newest first with filters and a cursor. */
export function AlertHistory() {
  const ws = useWorkspace();
  const canDest = useCan("alert.destination.read");
  const [filters, setFilters] = useState<HistoryFilters>({});
  const q = useAlertHistory(filters);
  const rules = useAlertRules(true);
  const dests = useAlertDestinations(canDest);
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = Object.values(filters).some(Boolean);
  const set = (k: keyof HistoryFilters, v: string) =>
    setFilters((f) => {
      const next = { ...f } as Record<string, string | undefined>;
      if (v) next[k] = v;
      else delete next[k];
      return next as HistoryFilters;
    });
  const slug = (id: string | null) =>
    id ? (ws.projects.find((p) => p.id === id)?.slug ?? id) : null;

  return (
    <section aria-label="Alert history">
      <div className="al-filters" role="group" aria-label="Filter the history">
        <label className="field">
          Status
          <select value={filters.status ?? ""} onChange={(e) => set("status", e.target.value)}>
            <option value="">Any</option>
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {STATUS_FILTER_WORD[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Trigger
          <select value={filters.trigger ?? ""} onChange={(e) => set("trigger", e.target.value)}>
            <option value="">Any</option>
            {TRIGGERS.map((t) => (
              <option key={t} value={t}>
                {TRIGGER_WORDS[t].short}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Rule
          <select value={filters.rule_id ?? ""} onChange={(e) => set("rule_id", e.target.value)}>
            <option value="">Any</option>
            {(rules.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        {canDest && (
          <label className="field">
            Destination
            <select
              value={filters.destination_id ?? ""}
              onChange={(e) => set("destination_id", e.target.value)}
            >
              <option value="">Any</option>
              {(dests.data?.items ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {filtered && (
          <button type="button" onClick={() => setFilters({})}>
            Clear filters
          </button>
        )}
      </div>
      {q.isPending ? (
        <Loading label="Loading history" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <div className="al-empty" data-testid="history-empty">
          <Empty
            motif="none"
            title={filtered ? "No alerts match these filters" : "Nothing has been sent yet"}
          >
            {filtered
              ? "Clear a filter to see more."
              : "When a rule fires, each alert and what happened to it is listed here."}
          </Empty>
        </div>
      ) : (
        <>
          <div className="table-wrap">
            <table className="al-table">
              <caption className="sr-only">Alerts, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Rule</th>
                  <th scope="col">Destination</th>
                  <th scope="col">Trigger</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Status</th>
                  <th scope="col">Detail</th>
                  <th scope="col">Open</th>
                </tr>
              </thead>
              <tbody>
                {items.map((n) => (
                  <Row
                    key={n.id}
                    n={n}
                    workspace={ws.workspace.slug}
                    projectSlug={slug(n.project_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="sec-more">
            {q.hasNextPage ? (
              <button
                type="button"
                disabled={q.isFetchingNextPage}
                onClick={() => void q.fetchNextPage()}
              >
                {q.isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            ) : (
              <span className="muted">End of list</span>
            )}
          </p>
        </>
      )}
    </section>
  );
}

function Row({
  n,
  workspace,
  projectSlug,
}: {
  n: AlertNotification;
  workspace: string;
  projectSlug: string | null;
}) {
  const meta = notificationStatus(n);
  // next_attempt_at means something only while the alert is still waiting.
  const next =
    n.status === "pending" && n.next_attempt_at ? `Next try ${when(n.next_attempt_at)}.` : "";
  const detail =
    n.status === "skipped"
      ? skipWords(n.skip_reason)
      : [errorCodeWords(n.error_code), next].filter(Boolean).join(" ");
  const run =
    n.run_id && projectSlug
      ? `/w/${workspace}/projects/${encodeURIComponent(projectSlug)}/runs/${n.run_id}`
      : null;
  return (
    <tr data-testid="history-row" data-status={n.status}>
      <td title={n.created_at}>{when(n.created_at)}</td>
      <td>{n.rule ? n.rule.name : <span className="muted">Rule deleted</span>}</td>
      <td>
        {n.destination ? (
          <>
            {n.destination.name ?? <span className="muted">Hidden for your role</span>}{" "}
            <span className="muted">({TYPE_WORD[n.destination.type]})</span>
          </>
        ) : (
          <span className="muted">Destination deleted</span>
        )}
      </td>
      <td>{triggerShort(n.trigger)}</td>
      <td>
        <SecuritySeverityMark severity={n.severity} />
      </td>
      <td>
        <StatusMark meta={meta} />
      </td>
      <td className="al-detail">
        {detail || (n.delivered_at ? `Delivered ${when(n.delivered_at)}.` : "")}
      </td>
      <td className="al-links">
        {run && n.finding_id ? (
          <Link href={findingHref(workspace, projectSlug!, n.run_id!, n.finding_id)}>Finding</Link>
        ) : null}
        {run && <Link href={run}>Run</Link>}
        {n.approval_id && (
          <Link href={`${approvalsPath(workspace)}?approval=${encodeURIComponent(n.approval_id)}`}>
            Approval
          </Link>
        )}
      </td>
    </tr>
  );
}
