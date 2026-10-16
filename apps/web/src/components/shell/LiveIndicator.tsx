"use client";

import { shell } from "@/copy/shell";
import { useProjectId, useWorkspaceOptional } from "../WorkspaceProvider";
import { useRuns } from "@/lib/queries";
import type { Base } from "@/lib/routes";

function Inner({ base }: { base: Base }) {
  const project = useProjectId(base.project);
  // One light request on the runs list's own cache key and 15 s poll; the API has no total, so no count (D13).
  const running = useRuns({ project, statuses: ["RUNNING"] }, 1);
  const on = (running.data?.pages[0]?.items.length ?? 0) > 0;
  // A dot beside Runs with an accessible name, only while something is running. No word on screen, nothing when idle.
  return (
    <span className="live" role="status" aria-live="off" data-on={on ? "true" : undefined}>
      {on && (
        <>
          <span className="live-dot" aria-hidden="true" />
          <span className="sr-only">{shell.recording}</span>
        </>
      )}
    </span>
  );
}

export function LiveIndicator({ base }: { base: Base }) {
  const ws = useWorkspaceOptional();
  if (!ws) return null;
  return <Inner base={base} />;
}
