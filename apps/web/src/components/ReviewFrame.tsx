"use client";

import { usePathname, useRouter } from "next/navigation";
import { review } from "@/copy/review";
import { usePendingCount } from "@/lib/policyApi";
import { approvalsPath } from "@/lib/routes";
import { securityPath } from "@/lib/security";
import { Tabs, tabIds } from "./Tabs";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/review.css";

type ReviewTab = "approvals" | "security";

/**
 * The frame of Review: the title line (h1 "Review" and, when requests wait, how many), then the shared tab list. A tab the
 * role may not read is absent. The two routes keep their addresses; choosing a tab goes to the other route, and the active
 * tab is whichever route is open, so a link to an approval or a filtered findings list opens the right tab.
 */
export function ReviewFrame({ children }: { children: React.ReactNode }) {
  const ws = useWorkspace();
  const path = usePathname();
  const router = useRouter();
  const canApprovals = useCan("approval.read");
  const canSecurity = useCan("run.read");
  const pending = usePendingCount(canApprovals);
  const slug = ws.workspace.slug;
  const hrefs: Record<ReviewTab, string> = {
    approvals: approvalsPath(slug),
    security: securityPath(slug),
  };
  const active: ReviewTab = path.startsWith(hrefs.approvals) ? "approvals" : "security";
  const waiting = pending.data ?? 0;
  const items = [
    ...(canApprovals ? [{ id: "approvals" as const, label: review.tabs.approvals }] : []),
    ...(canSecurity ? [{ id: "security" as const, label: review.tabs.security }] : []),
  ];
  return (
    <div className="review">
      <div className="review-head">
        <h1>{review.title}</h1>
        {canApprovals && waiting > 0 && <p className="review-count">{review.waiting(waiting)}</p>}
      </div>
      {items.length > 0 && (
        <Tabs
          label={review.tabsLabel}
          idBase="review"
          className="tabs-ruled"
          items={items}
          value={active}
          onChange={(t) => router.push(hrefs[t])}
        />
      )}
      <div role="tabpanel" className="review-panel" {...tabIds("review", active)}>
        {children}
      </div>
    </div>
  );
}
