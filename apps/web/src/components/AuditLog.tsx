"use client";

import { useAudit } from "@/lib/admin";
import { settings } from "@/copy/settings";
import { Lede, Section, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";

export function AuditLog() {
  const audit = useAudit(true);
  const rows = audit.data?.pages.flatMap((p) => p.items) ?? [];
  const c = settings.audit;
  return (
    <>
      <Lede>{c.lede}</Lede>
      <Section title="Entries">
        {audit.isPending ? (
          <Loading label="Loading audit log" />
        ) : audit.error ? (
          <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
        ) : rows.length === 0 ? (
          <Empty title={c.empty} />
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">{c.caption}</caption>
              <thead>
                <tr>
                  <th>{c.heads.when}</th>
                  <th>{c.heads.actor}</th>
                  <th>{c.heads.action}</th>
                  <th>{c.heads.outcome}</th>
                  <th>{c.heads.resource}</th>
                  <th>{c.heads.details}</th>
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
            {audit.isFetchingNextPage ? c.loadingMore : c.more}
          </button>
        )}
      </Section>
    </>
  );
}
