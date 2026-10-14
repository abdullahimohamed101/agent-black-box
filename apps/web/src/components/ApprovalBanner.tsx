"use client";

import Link from "next/link";
import { approvalsPath } from "@/lib/routes";
import { leftText } from "@/lib/policy";
import "../app/policy.css";

/**
 * Under the run's headline while it waits for a person: what it is waiting on and, for a role that may read approvals,
 * the time left and a link to decide. Amber glyph, ink words; a wait is not a failure. Others read the same sentence
 * without the link and the clock.
 */
export function ApprovalBanner({
  capability,
  approvalId,
  workspace,
  canOpen,
  leftSeconds,
}: {
  capability: string | null;
  approvalId: string | null;
  workspace: string | null;
  canOpen: boolean;
  leftSeconds: number | null;
}) {
  const left = leftText(leftSeconds);
  return (
    <p className="approval-banner" role="status" data-testid="approval-banner">
      <span>
        <span className="pol-glyph" aria-hidden="true">
          ❙❙
        </span>{" "}
        Waiting for a person to approve{capability ? ` ${capability}` : " an action"}
        {left ? ` · ${left}` : ""}
      </span>
      {canOpen && workspace ? (
        <Link
          href={`${approvalsPath(workspace)}${approvalId ? `?approval=${encodeURIComponent(approvalId)}` : ""}`}
        >
          Open Approvals
        </Link>
      ) : (
        <span className="muted">Someone with approval rights decides this request.</span>
      )}
    </p>
  );
}
