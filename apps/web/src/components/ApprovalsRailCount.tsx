"use client";

import { usePendingCount } from "@/lib/policyApi";
import "../app/policy.css";

/**
 * The number of requests waiting for a decision, beside "Approvals" in the rail. It is a request of its own (every ~30 s,
 * visible tab only) and renders nothing until the answer arrives, so the server render and the first client render match.
 * A failed read shows nothing rather than a wrong number.
 */
export function ApprovalsRailCount() {
  const q = usePendingCount(true);
  const n = q.data;
  if (n == null || n <= 0) return null;
  return (
    <>
      <span className="rail-count" aria-hidden="true" data-testid="approvals-count">
        {n > 99 ? "99+" : n}
      </span>
      <span className="sr-only">, {n} waiting</span>
    </>
  );
}
