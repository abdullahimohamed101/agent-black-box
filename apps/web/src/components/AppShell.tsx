"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { analyticsPath, projectPath, runsPath, type Base } from "@/lib/routes";
import { SignOutButton } from "./SignOutButton";
import { useWorkspaceOptional } from "./WorkspaceProvider";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

export function AppShell({ base, children }: { base: Base; children: React.ReactNode }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const dash = projectPath(base);
  const runs = runsPath(base);
  const analytics = analyticsPath(base);
  const settings = `/w/${base.workspace}/settings/members`;
  const cur = (href: string, exact: boolean) =>
    (exact ? path === href : path === href || path.startsWith(`${href}/`)) ? "page" : undefined;
  const project = ws?.projects.find((p) => p.slug === base.project || p.id === base.project);
  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <header className="topbar">
        <Link href="/" className="brand">
          Agent Black Box
        </Link>
        <WorkspaceSwitcher />
        <span className="crumb muted" title="Project">
          {base.project === "all" ? "all projects" : (project?.name ?? base.project)}
        </span>
        <nav aria-label="Primary">
          <Link href={dash} aria-current={cur(dash, true)}>
            Dashboard
          </Link>
          <Link href={runs} aria-current={cur(runs, false)}>
            Runs
          </Link>
          <Link href={analytics} aria-current={cur(analytics, false)}>
            Analytics
          </Link>
          {ws?.mode === "session" && (
            <Link
              href={settings}
              aria-current={path.startsWith(`/w/${base.workspace}/settings`) ? "page" : undefined}
            >
              Settings
            </Link>
          )}
        </nav>
        {ws?.mode === "session" && ws.user && (
          <div className="who">
            <span className="muted" title={ws.role ?? undefined}>
              {ws.user.email}
            </span>
            {/* A POST: signing out is never a GET (a cross-site link must not be able to do it). */}
            <SignOutButton />
          </div>
        )}
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
