"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { review } from "@/copy/review";
import { useEffect, useRef } from "react";
import { ApiRequestError } from "@/lib/api/client";
import { formatRelative, formatTimestamp } from "@/lib/format";
import {
  APPROVAL_TABS,
  ageSeconds,
  leftText,
  parseApprovalTab,
  type ApprovalSummary,
  type ApprovalTab,
} from "@/lib/policy";
import { useApprovals, useCachedPendingCount } from "@/lib/policyApi";
import { useNow } from "@/lib/useNow";
import { ApprovalDetailPanel } from "./ApprovalDetailPanel";
import { RiskMark, StateMark } from "./PolicyMarks";
import { Forbidden } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { Tabs, tabIds } from "./Tabs";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/policy.css";

/**
 * The Approvals tab of Review: requests agents made for a person to decide. The URL holds the tab (`?state=`) and the
 * open request (`?approval=`), so a link opens exactly this view. The pending list re-reads every ~10 s while the tab is
 * visible. Rows show the API's scrubbed fields only; the detail panel is where a person reads the whole intent and decides.
 */
export function ApprovalsPage({ nowProp }: { nowProp?: number }) {
  const ws = useWorkspace();
  const canRead = useCan("approval.read");
  const canDecide = useCan("approval.decide");
  const pathname = usePathname();
  const sp = useSearchParams();
  const params = new URLSearchParams(sp?.toString() ?? "");
  const tab = parseApprovalTab(params.get("state"));
  const openId = params.get("approval");
  const q = useApprovals(tab, null, canRead);
  const waiting = useCachedPendingCount() ?? 0;
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const hasWaiting = items.some((a) => a.state === "REQUESTED");
  const clock = useNow(1000, hasWaiting);
  const now = nowProp ?? clock;
  const received = q.dataUpdatedAt;
  const lastFocus = useRef<string | null>(null);

  const go = (next: { state?: ApprovalTab; approval?: string | null }) => {
    const p = new URLSearchParams();
    const st = next.state ?? tab;
    if (st !== "pending") p.set("state", st);
    const ap = next.approval === undefined ? openId : next.approval;
    if (ap) p.set("approval", ap);
    const qs = p.toString();
    window.history.pushState(null, "", qs ? `${pathname}?${qs}` : pathname);
  };
  const hrefFor = (state: ApprovalTab, approval: string | null) => {
    const p = new URLSearchParams();
    if (state !== "pending") p.set("state", state);
    if (approval) p.set("approval", approval);
    const qs = p.toString();
    return qs ? `${pathname}?${qs}` : pathname;
  };
  const close = () => {
    const id = openId;
    go({ approval: null });
    lastFocus.current = id;
  };
  useEffect(() => {
    // Back to the row that opened the panel (the panel's own close button is gone by now).
    if (!openId && lastFocus.current) {
      document.getElementById(`apr-open-${lastFocus.current}`)?.focus();
      lastFocus.current = null;
    }
  }, [openId, items.length]);

  if (!canRead || (q.error instanceof ApiRequestError && q.error.status === 403))
    return (
      <div className="apr-page">
        <Forbidden what={review.approvals.forbiddenWhat} />
      </div>
    );

  const projectSlug = (id: string) => ws.projects.find((p) => p.id === id)?.slug ?? id;
  const projectName = (id: string) => ws.projects.find((p) => p.id === id)?.name ?? "—";
  const c = review.approvals;

  return (
    <div className="apr-page">
      <Tabs
        label={c.stateLabel}
        idBase="apr-state"
        className="tabs-chip"
        items={APPROVAL_TABS.map((t) => ({
          id: t.key,
          label: t.label,
          count: t.key === "pending" && waiting > 0 ? String(waiting) : undefined,
        }))}
        value={tab}
        onChange={(t) => go({ state: t, approval: null })}
      />
      <div role="tabpanel" {...tabIds("apr-state", tab)}>
        <div className="apr-body" data-detail={openId ? "open" : undefined}>
          <div className="apr-list">
            {q.isPending ? (
              <Loading label="Loading approvals" />
            ) : q.isError ? (
              <ErrorState error={q.error} onRetry={() => void q.refetch()} />
            ) : items.length === 0 ? (
              <div className="apr-empty" data-testid="approvals-empty">
                <Empty title={c.empty[tab].title} motif="none">
                  {c.empty[tab].body}
                </Empty>
              </div>
            ) : (
              <>
                <div className="table-wrap">
                  <table className="apr-table">
                    <caption className="sr-only">{c.caption}</caption>
                    <thead>
                      <tr>
                        <th scope="col">{c.heads.request}</th>
                        <th scope="col">{c.heads.risk}</th>
                        <th scope="col">{c.heads.state}</th>
                        <th scope="col">{c.heads.requested}</th>
                        <th scope="col">{c.heads.left}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((a) => (
                        <Row
                          key={a.id}
                          a={a}
                          selected={a.id === openId}
                          now={now}
                          received={received}
                          href={hrefFor(tab, a.id)}
                          onOpen={() => go({ approval: a.id })}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted apr-count-line" aria-live="polite">
                  {c.loaded(items.length, !q.hasNextPage)}
                </p>
                {q.hasNextPage && (
                  <p className="sec-more">
                    <button
                      type="button"
                      disabled={q.isFetchingNextPage}
                      onClick={() => void q.fetchNextPage()}
                    >
                      {q.isFetchingNextPage ? c.loadingMore : c.more}
                    </button>
                  </p>
                )}
              </>
            )}
          </div>
          {openId && (
            <ApprovalDetailPanel
              key={openId}
              id={openId}
              canDecide={canDecide}
              now={now}
              runHref={(runId, projectId) =>
                `/w/${ws.workspace.slug}/projects/${projectSlug(projectId)}/runs/${runId}`
              }
              projectName={projectName}
              onClose={close}
            />
          )}
        </div>
      </div>
      <p className="muted apr-foot">{c.foot}</p>
    </div>
  );
}

function Row({
  a,
  selected,
  now,
  received,
  href,
  onOpen,
}: {
  a: ApprovalSummary;
  selected: boolean;
  now: number;
  received: number;
  href: string;
  onOpen: () => void;
}) {
  const left = a.state === "REQUESTED" ? leftText(ageSeconds(a.seconds_left, received, now)) : null;
  return (
    <tr data-testid="approval-row" data-state={a.state} data-selected={selected || undefined}>
      <td className="apr-req">
        <Link
          id={`apr-open-${a.id}`}
          href={href}
          className="apr-cap"
          aria-current={selected ? "true" : undefined}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            onOpen();
          }}
        >
          {a.capability}
        </Link>
        <p className="apr-res" title={a.resource ?? undefined}>
          {a.resource || "—"}
        </p>
      </td>
      <td>
        <RiskMark risk={a.risk_class} />
      </td>
      <td>
        <StateMark approval={a} />
      </td>
      <td title={formatTimestamp(a.requested_at)}>{formatRelative(a.requested_at, now)}</td>
      <td className="apr-left" data-testid="time-left" title={formatTimestamp(a.expires_at)}>
        {a.state === "EXPIRED" ? review.approvals.expired : (left ?? "—")}
      </td>
    </tr>
  );
}
