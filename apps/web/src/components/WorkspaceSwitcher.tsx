"use client";

import Link from "next/link";
import { useWorkspaceOptional } from "./WorkspaceProvider";

/**
 * Memberships come from `/v1/me`; each entry opens that workspace's dashboard (every tab keeps its own, D8).
 * A disclosure of links rather than a <select>: no listbox roles to clash with the page's own listboxes.
 */
export function WorkspaceSwitcher() {
  const ws = useWorkspaceOptional();
  if (!ws || ws.memberships.length === 0) return null;
  if (ws.memberships.length === 1) {
    return (
      <span className="crumb muted" title="Workspace">
        {ws.workspace.name}
      </span>
    );
  }
  return (
    <details className="switcher crumb">
      <summary aria-label={`Workspace: ${ws.workspace.name}. Switch workspace`}>
        {ws.workspace.name}
      </summary>
      <ul aria-label="Your workspaces">
        {ws.memberships.map((m) => (
          <li key={m.slug}>
            <Link
              href={`/w/${encodeURIComponent(m.slug)}/projects/all`}
              aria-current={m.slug === ws.workspace.slug ? "true" : undefined}
            >
              {m.name}
            </Link>{" "}
            <span className="muted">{m.role}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
