"use client";

import { useAudit } from "@/lib/admin";
import { when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";

export function AuditLog() {
  const audit = useAudit(true);
  const rows = audit.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <>
      <h1>Audit log</h1>
      <p className="muted">Administrative actions and refused attempts, newest first.</p>
      {audit.isPending ? (
        <Loading label="Loading audit log" />
      ) : audit.error ? (
        <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
      ) : rows.length === 0 ? (
        <Empty title="Nothing recorded yet" />
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Audit log</caption>
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Outcome</th>
                <th>Resource</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>{when(r.occurred_at)}</td>
                  <td className="meta">
                    {r.actor_kind}: {r.actor_id}
                  </td>
                  <td>{r.action}</td>
                  <td>{r.outcome}</td>
                  <td className="meta">
                    {r.resource_kind ? `${r.resource_kind} ${r.resource_id ?? ""}` : "—"}
                  </td>
                  <td>
                    {Object.keys(r.details).length > 0 ? (
                      <details>
                        <summary>Show</summary>
                        <pre className="meta">{JSON.stringify(r.details, null, 2)}</pre>
                      </details>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {audit.hasNextPage && (
        <button
          type="button"
          disabled={audit.isFetchingNextPage}
          onClick={() => void audit.fetchNextPage()}
        >
          {audit.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}
