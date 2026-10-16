"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { shell } from "@/copy/shell";
import { projectPath, runsPath, type Base } from "@/lib/routes";
import { useWorkspaceOptional } from "../WorkspaceProvider";
import { useMenu } from "./useMenu";

const Caret = () => (
  <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">
    <path
      d="M4 6l4 4 4-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/**
 * "workspace / project": the one control that replaces the two switcher boxes. A disclosure of links (no listbox or menu
 * roles, so it cannot clash with the page's own); choosing a scope opens Runs in it. Without a workspace provider (never on
 * a real page) it is just the text.
 */
export function ScopeControl({ base }: { base: Base }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const { open, attach, toggle, onBlur } = useMenu(path);
  const current =
    base.project === "all"
      ? undefined
      : ws?.projects.find((p) => p.slug === base.project || p.id === base.project);
  const project =
    base.project === "all" ? shell.scope.allProjects : (current?.name ?? base.project);
  const workspace = ws?.workspace.name ?? base.workspace;
  const label = `${workspace} / ${project}`;
  if (!ws) return <span className="scope-static">{label}</span>;
  const here = (slug: string, p: string) => runsPath({ workspace: slug, project: p });
  return (
    <div className="menu" ref={attach} onBlur={onBlur}>
      <button
        type="button"
        className="menu-trigger scope-button"
        aria-expanded={open}
        aria-controls="scope-panel"
        title={shell.scope.hint}
        onClick={toggle}
      >
        <span className="scope-text">{label}</span>
        <Caret />
      </button>
      {open && (
        <div id="scope-panel" className="menu-panel">
          {ws.memberships.length > 1 && (
            <>
              <p className="menu-label">{shell.scope.workspaces}</p>
              <ul className="menu-list" aria-label={shell.scope.workspacesList}>
                {ws.memberships.map((m) => (
                  <li key={m.slug}>
                    <Link
                      href={here(m.slug, "all")}
                      aria-current={m.slug === ws.workspace.slug ? "true" : undefined}
                    >
                      <span>{m.name}</span>
                      <span className="menu-aside">{m.role.toLowerCase()}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="menu-label">{shell.scope.projects}</p>
          <ul className="menu-list" aria-label={shell.scope.projectsList}>
            <li>
              <Link
                href={here(base.workspace, "all")}
                aria-current={base.project === "all" ? "true" : undefined}
              >
                {shell.scope.allProjects}
              </Link>
            </li>
            {ws.projects.map((p) => (
              <li key={p.id}>
                <Link
                  href={`${projectPath({ workspace: base.workspace, project: p.slug })}/runs`}
                  aria-current={
                    p.slug === base.project || p.id === base.project ? "true" : undefined
                  }
                >
                  {p.name}
                </Link>
              </li>
            ))}
          </ul>
          {ws.projects.length === 0 && <p className="menu-note">{shell.scope.noProjects}</p>}
        </div>
      )}
    </div>
  );
}
