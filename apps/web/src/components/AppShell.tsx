"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { securityPath } from "@/lib/security";
import { analyticsPath, experimentsPath, projectPath, runsPath, type Base } from "@/lib/routes";
import { SignOutButton } from "./SignOutButton";
import { useWorkspaceOptional } from "./WorkspaceProvider";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { Brand } from "./shell/Brand";
import { Crumbs } from "./shell/Crumbs";
import { LiveIndicator } from "./shell/LiveIndicator";
import { ProjectSwitcher } from "./shell/ProjectSwitcher";
import { TopbarSearch } from "./shell/TopbarSearch";

/**
 * The application frame: a left rail (brand, scope, primary navigation, who), a slim top bar (where you are, whether
 * anything is recording) and the page. DOM order is the focus order: skip link, rail, top bar, main.
 */
export function AppShell({ base, children }: { base: Base; children: React.ReactNode }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const dash = projectPath(base);
  const runs = runsPath(base);
  const analytics = analyticsPath(base);
  const experiments = experimentsPath(base);
  const settings = `/w/${base.workspace}/settings/members`;
  // In a project the link is the same page pre-filtered to it (the project's Security view).
  const securityBase = `/w/${base.workspace}/security`;
  const security = securityPath(base.workspace, base.project === "all" ? null : base.project);
  const cur = (href: string, exact: boolean) =>
    (exact ? path === href : path === href || path.startsWith(`${href}/`)) ? "page" : undefined;
  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <aside className="rail">
        <Brand />
        <div className="scope">
          <WorkspaceSwitcher />
          <ProjectSwitcher base={base} />
        </div>
        <nav aria-label="Primary" className="nav">
          <Link href={dash} aria-current={cur(dash, true)}>
            Overview
          </Link>
          <Link href={runs} aria-current={cur(runs, false)}>
            Runs
          </Link>
          {(!ws || ws.permissions.includes("run.read")) && (
            <Link href={security} aria-current={cur(securityBase, false)}>
              Security
            </Link>
          )}
          <Link href={analytics} aria-current={cur(analytics, false)}>
            Analytics
          </Link>
          <Link href={experiments} aria-current={cur(experiments, false)}>
            Experiments
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
          <div className="rail-foot who">
            <span className="muted" title={ws.role ?? undefined}>
              {ws.user.email}
            </span>
            {/* A POST: signing out is never a GET (a cross-site link must not be able to do it). */}
            <SignOutButton />
          </div>
        )}
      </aside>
      <header className="topbar">
        <Crumbs base={base} />
        <TopbarSearch base={base} />
        <LiveIndicator base={base} />
      </header>
      <main id="main" tabIndex={-1} className="page">
        {children}
      </main>
    </div>
  );
}
