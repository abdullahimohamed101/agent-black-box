"use client";

import Link from "next/link";
import { projectPath, type Base } from "@/lib/routes";
import { useWorkspaceOptional } from "../WorkspaceProvider";

/**
 * The project in scope. A disclosure of links like the workspace switcher (no listbox role to clash with the run page's
 * own listboxes). Without a workspace provider (never on a real page) it is just the name.
 */
export function ProjectSwitcher({ base }: { base: Base }) {
  const ws = useWorkspaceOptional();
  const current =
    base.project === "all"
      ? undefined
      : ws?.projects.find((p) => p.slug === base.project || p.id === base.project);
  const label = base.project === "all" ? "All projects" : (current?.name ?? base.project);
  if (!ws || ws.projects.length === 0) {
    return (
      <span className="switcher-static" data-label="Project" title="Project">
        {label}
      </span>
    );
  }
  return (
    <details className="switcher" data-label="Project">
      <summary aria-label={`Project: ${label}. Switch project`}>{label}</summary>
      <ul aria-label="Projects">
        <li>
          <Link
            href={projectPath({ workspace: base.workspace, project: "all" })}
            aria-current={base.project === "all" ? "true" : undefined}
          >
            All projects
          </Link>
        </li>
        {ws.projects.map((p) => (
          <li key={p.id}>
            <Link
              href={projectPath({ workspace: base.workspace, project: p.slug })}
              aria-current={p.slug === base.project || p.id === base.project ? "true" : undefined}
            >
              {p.name}
            </Link>
          </li>
        ))}
      </ul>
    </details>
  );
}
