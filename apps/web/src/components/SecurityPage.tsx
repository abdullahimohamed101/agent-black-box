"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { review } from "@/copy/review";
import { ApiRequestError } from "@/lib/api/client";
import { formatRelative, formatTimestamp } from "@/lib/format";
import { useSecurityDetectors, useSecurityFindings } from "@/lib/queries";
import {
  RANGE_LABEL,
  SEVERITY_ORDER,
  detectorName,
  findingHref,
  hasSecurityFilters,
  NO_SECURITY_FILTERS,
  parseSecurityFilters,
  rangeBounds,
  securityQueryString,
  securitySeverityMeta,
  toggleValue,
  type RangeKey,
  type SecurityFilters,
} from "@/lib/security";
import { DetectorList } from "./SecurityFindings";
import { SecuritySeverityMark } from "./SecuritySeverity";
import { Forbidden } from "./SettingsKit";
import { Empty, ErrorState } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/runs-list.css";
import "../app/security.css";

const NARROW = "(max-width: 52rem)";
const isNarrow = () => window.matchMedia?.(NARROW).matches ?? false;
const subscribeNarrow = (notify: () => void) => {
  const m = window.matchMedia?.(NARROW);
  m?.addEventListener("change", notify);
  return () => m?.removeEventListener("change", notify);
};

type FacetValue = { value: string; label: string | null; count: number };

function FacetGroup({
  title,
  items,
  on,
  name,
  onToggle,
}: {
  title: string;
  items: readonly FacetValue[];
  on: (value: string) => boolean;
  name: (v: FacetValue) => string;
  onToggle: (value: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="facet">
      <h3>{title}</h3>
      <ul>
        {items.map((v) => (
          <li key={v.value}>
            <button
              type="button"
              className="facet-button"
              aria-pressed={on(v.value)}
              onClick={() => onToggle(v.value)}
            >
              {name(v)}
              <span className="muted"> ({v.count})</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="table-wrap" aria-busy="true" role="status" aria-live="polite">
      <span className="sr-only">{review.security.loading}</span>
      <table className="sec-table" aria-hidden="true">
        <tbody>
          {[0, 1, 2, 3, 4].map((i) => (
            <tr key={i}>
              <td>
                <span className="sec-skel" style={{ width: "4rem" }} />
              </td>
              <td>
                <span className="sec-skel" style={{ width: "70%" }} />
              </td>
              <td>
                <span className="sec-skel" style={{ width: "8rem" }} />
              </td>
              <td>
                <span className="sec-skel" style={{ width: "3rem" }} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The workspace Security view: recent security findings across projects, newest run first. The URL holds every filter
 * (shareable; back and forward work). Rows carry the API's fixed strings only; a row links to the run with that finding's
 * evidence open, where the role rules apply.
 */
export function SecurityPage({ now: nowProp }: { now?: number }) {
  const ws = useWorkspace();
  const canRead = useCan("run.read");
  const pathname = usePathname();
  const sp = useSearchParams();
  const filters = parseSecurityFilters(new URLSearchParams(sp?.toString() ?? ""));
  const project = filters.project
    ? ws.projects.find((p) => p.slug === filters.project || p.id === filters.project)
    : undefined;
  const projectId = filters.project ? (project?.id ?? filters.project) : null;
  const [mountedAt] = useState(() => nowProp ?? Date.now());
  const bounds = rangeBounds(filters, mountedAt);
  const q = useSecurityFindings(
    { severity: filters.severity, detector: filters.detector, projectId, ...bounds },
    canRead,
  );
  const detectors = useSecurityDetectors(projectId, canRead);

  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, () => false);
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? !narrow;

  // The URL is the single source of truth: a change is a history entry, and the page re-reads it.
  const set = (next: SecurityFilters) => {
    const qs = securityQueryString(next);
    window.history.pushState(null, "", qs ? `${pathname}?${qs}` : pathname);
  };
  const slugOf = (id: string) => ws.projects.find((p) => p.id === id)?.slug ?? id;

  if (!canRead || (q.error instanceof ApiRequestError && q.error.status === 403))
    return <Forbidden what={review.security.forbiddenWhat} />;

  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const facets = q.data?.pages[0]?.facets;
  const filtered = hasSecurityFilters(filters);
  const settled = !q.isPending && !q.isError;
  const activeCount =
    filters.severity.length +
    filters.detector.length +
    (filters.project ? 1 : 0) +
    (filters.range !== "all" || filters.since || filters.until ? 1 : 0);

  return (
    <div className="sec-page">
      {settled && items.length === 0 && !filtered ? (
        <div className="sec-empty" data-testid="security-clean">
          <Empty title={review.security.cleanTitle} motif="none">
            {review.security.cleanBody}
          </Empty>
          {detectors.data ? (
            <DetectorList items={detectors.data} />
          ) : detectors.isError ? (
            <ErrorState error={detectors.error} onRetry={() => void detectors.refetch()} />
          ) : null}
        </div>
      ) : (
        <div className="runs-body">
          <section className="facets" aria-label="Narrow the results">
            <details
              className="facets-box"
              open={open}
              onToggle={(e) => setChosen((e.currentTarget as HTMLDetailsElement).open)}
            >
              <summary>
                Narrow results
                {activeCount > 0 && <span className="muted"> ({activeCount} on)</span>}
              </summary>
              <FacetGroup
                title="Severity"
                items={[...(facets?.severity ?? [])].sort(
                  (a, b) =>
                    SEVERITY_ORDER.indexOf(a.value as never) -
                    SEVERITY_ORDER.indexOf(b.value as never),
                )}
                on={(v) => filters.severity.includes(v)}
                name={(v) => securitySeverityMeta(v.value).label}
                onToggle={(v) => set({ ...filters, severity: toggleValue(filters.severity, v) })}
              />
              <FacetGroup
                title="Detector"
                items={facets?.detector ?? []}
                on={(v) => filters.detector.includes(v)}
                name={(v) => detectorName(v.value)}
                onToggle={(v) => set({ ...filters, detector: toggleValue(filters.detector, v) })}
              />
              <FacetGroup
                title="Project"
                items={facets?.project ?? []}
                on={(v) => projectId === v}
                name={(v) => v.label ?? v.value}
                onToggle={(v) => set({ ...filters, project: projectId === v ? "" : slugOf(v) })}
              />
              <div className="facet">
                <h3>Run started</h3>
                <ul>
                  {(Object.keys(RANGE_LABEL) as RangeKey[]).map((k) => (
                    <li key={k}>
                      <button
                        type="button"
                        className="facet-button"
                        aria-pressed={filters.range === k && !filters.since && !filters.until}
                        onClick={() => set({ ...filters, range: k, since: "", until: "" })}
                      >
                        {RANGE_LABEL[k]}
                      </button>
                    </li>
                  ))}
                </ul>
                {(filters.since || filters.until) && (
                  <p className="muted">
                    Custom range from a shared link.{" "}
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => set({ ...filters, since: "", until: "" })}
                    >
                      Clear range
                    </button>
                  </p>
                )}
              </div>
              {filtered && (
                <button type="button" onClick={() => set(NO_SECURITY_FILTERS)}>
                  Clear filters
                </button>
              )}
            </details>
          </section>
          <div className="runs-main">
            {q.isPending ? (
              <Skeleton />
            ) : q.isError ? (
              <ErrorState error={q.error} onRetry={() => void q.refetch()} />
            ) : items.length === 0 ? (
              <Empty
                title={review.security.noneTitle}
                motif="none"
                action={
                  <button type="button" onClick={() => set(NO_SECURITY_FILTERS)}>
                    Clear filters
                  </button>
                }
              >
                {review.security.noneBody}
              </Empty>
            ) : (
              <>
                <div className="table-wrap">
                  <table className="sec-table">
                    <caption className="sr-only">{review.security.caption}</caption>
                    <thead>
                      <tr>
                        <th scope="col">{review.security.heads.severity}</th>
                        <th scope="col">{review.security.heads.finding}</th>
                        <th scope="col">{review.security.heads.run}</th>
                        <th scope="col">{review.security.heads.when}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((r) => (
                        <tr key={r.id} data-testid="security-row" data-severity={r.severity}>
                          <td>
                            <SecuritySeverityMark severity={r.severity} />
                          </td>
                          <td className="title">
                            {r.title}
                            {r.rule && <p className="sec-rule">Rule: {r.rule}</p>}
                          </td>
                          <td className="run">
                            <Link
                              href={findingHref(ws.workspace.slug, r.project_slug, r.run_id, r.id)}
                            >
                              {r.run_name ?? r.run_id}
                            </Link>
                          </td>
                          <td title={formatTimestamp(r.run_started_at)}>
                            {formatRelative(r.run_started_at, nowProp)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted sec-count-line" aria-live="polite">
                  {review.security.loaded(items.length, !q.hasNextPage)}
                </p>
                {q.hasNextPage && (
                  <p className="sec-more">
                    <button
                      type="button"
                      disabled={q.isFetchingNextPage}
                      onClick={() => void q.fetchNextPage()}
                    >
                      {q.isFetchingNextPage ? "Loading…" : "Load more"}
                    </button>
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
