"use client";

import { useProjectId, useWorkspaceOptional } from "../WorkspaceProvider";
import { useRuns } from "@/lib/queries";
import type { Base } from "@/lib/routes";

function Inner({ base }: { base: Base }) {
  const project = useProjectId(base.project);
  // One light request on the runs list's own cache key and 15 s poll; the API has no total, so no count (D13).
  const running = useRuns({ project, statuses: ["RUNNING"] }, 1);
  const known = running.data !== undefined;
  const on = (running.data?.pages[0]?.items.length ?? 0) > 0;
  return (
    <span
      className="live-indicator"
      role="status"
      aria-live="off"
      data-on={on ? "true" : undefined}
    >
      {known && (
        <>
          <span className="live-dot" aria-hidden="true" />
          {on ? "Recording" : "Idle"}
        </>
      )}
    </span>
  );
}

/** "Recording" while at least one run is RUNNING in scope, else "Idle". Nothing without a workspace. */
export function LiveIndicator({ base }: { base: Base }) {
  const ws = useWorkspaceOptional();
  if (!ws) return null;
  return <Inner base={base} />;
}
