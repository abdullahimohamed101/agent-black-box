"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
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
import { useApprovals } from "@/lib/policyApi";
import { useNow } from "@/lib/useNow";
import { ApprovalDetailPanel } from "./ApprovalDetailPanel";
import { RiskMark, StateMark } from "./PolicyMarks";
import { Forbidden } from "./SettingsKit";
import { Empty, ErrorState } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/runs-list.css";
import "../app/policy.css";

const EMPTY_TEXT: Record<ApprovalTab, { title: string; body: string }> = {
  pending: {
    title: "Nothing is waiting for you",
    body: "When an agent asks a person to approve an action, the request appears here.",
  },
  decided: {
    title: "No decided requests yet",
    body: "Approved, denied, expired and cancelled requests are listed here.",
  },
  all: {
    title: "No approval requests yet",
    body: "Policies that require approval create a request when an agent asks to act.",
  },
};

function Skeleton() {
  return (
    <div className="table-wrap" aria-busy="true" role="status" aria-live="polite">
      <span className="sr-only">Loading approvals…</span>
      <table className="apr-table" aria-hidden="true">
        <tbody>
          {[0, 1, 2, 3, 4].map((i) => (
            <tr key={i}>
              {[12, 5, 6, 5, 5, 4, 5, 8].map((w, j) => (
                <td key={j}>
                  <span className="sec-skel" style={{ width: `${w}rem`, maxWidth: "100%" }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The workspace Approvals page: requests agents made for a person to decide. The URL holds the tab (`?state=`) and the
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
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const settled = !q.isPending && !q.isError;
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
        <PageHead />
        <Forbidden what="approvals" />
      </div>
    );

  const projectName = (id: string) => ws.projects.find((p) => p.id === id)?.name ?? "—";
  const projectSlug = (id: string) => ws.projects.find((p) => p.id === id)?.slug ?? id;

  return (
    <div className="apr-page">
      <PageHead
        note={
          settled && items.length > 0 ? (
            <p className="muted" aria-live="polite">
              {items.length} request{items.length === 1 ? "" : "s"} loaded
              {q.hasNextPage ? "" : " (end of list)"}
            </p>
          ) : null
        }
      />
      <nav className="apr-tabs" aria-label="Approval state">
        <ul>
          {APPROVAL_TABS.map((t) => (
            <li key={t.key}>
              <Link
                href={hrefFor(t.key, null)}
                aria-current={tab === t.key ? "page" : undefined}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                  e.preventDefault();
                  go({ state: t.key, approval: null });
                }}
              >
                {t.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="apr-body" data-detail={openId ? "open" : undefined}>
        <div className="apr-list">
          {q.isPending ? (
            <Skeleton />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          ) : items.length === 0 ? (
            <div className="apr-empty" data-testid="approvals-empty">
              <Empty title={EMPTY_TEXT[tab].title} motif="trace">
                {EMPTY_TEXT[tab].body}
              </Empty>
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table className="apr-table">
                  <caption className="sr-only">Approval requests, newest first</caption>
                  <thead>
                    <tr>
                      <th scope="col">Request</th>
                      <th scope="col">Risk</th>
                      <th scope="col">State</th>
                      <th scope="col">Run</th>
                      <th scope="col">Project</th>
                      <th scope="col">Requested</th>
                      <th scope="col">Time left</th>
                      <th scope="col">Policy</th>
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
                        runHref={`/w/${ws.workspace.slug}/projects/${projectSlug(a.project_id)}/runs/${a.run_id}`}
                        project={projectName(a.project_id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
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
      <p className="muted apr-foot">
        An approval authorises one action, once. Policies apply to agents that ask through the SDK
        guard.
      </p>
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
  runHref,
  project,
}: {
  a: ApprovalSummary;
  selected: boolean;
  now: number;
  received: number;
  href: string;
  onOpen: () => void;
  runHref: string;
  project: string;
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
      <td className="run">
        <Link href={runHref}>{`…${a.run_id.slice(-8)}`}</Link>
      </td>
      <td>{project}</td>
      <td title={formatTimestamp(a.requested_at)}>{formatRelative(a.requested_at, now)}</td>
      <td className="apr-left" data-testid="time-left" title={formatTimestamp(a.expires_at)}>
        {a.state === "EXPIRED" ? "Expired" : (left ?? "—")}
      </td>
      <td className="apr-policy">
        {a.policy_id} <span className="muted">v{a.policy_version}</span>
      </td>
    </tr>
  );
}

function PageHead({ note }: { note?: React.ReactNode }) {
  return (
    <div className="page-head">
      <h1>Approvals</h1>
      {note ?? (
        <p className="muted">
          Actions agents asked a person to approve. Each request shows what the agent wants to do,
          after secrets are scrubbed.
        </p>
      )}
    </div>
  );
}
