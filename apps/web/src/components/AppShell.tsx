"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { projectPath, runsPath, type Base } from "@/lib/routes";

export function AppShell({ base, children }: { base: Base; children: React.ReactNode }) {
  const path = usePathname();
  const dash = projectPath(base);
  const runs = runsPath(base);
  const cur = (href: string, exact: boolean) =>
    (exact ? path === href : path === href || path.startsWith(`${href}/`)) ? "page" : undefined;
  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <header className="topbar">
        <Link href="/" className="brand">
          Agent Black Box
        </Link>
        <span className="crumb muted" title="Workspace / project">
          {base.workspace} / {base.project === "all" ? "all projects" : base.project}
        </span>
        <nav aria-label="Primary">
          <Link href={dash} aria-current={cur(dash, true)}>
            Dashboard
          </Link>
          <Link href={runs} aria-current={cur(runs, false)}>
            Runs
          </Link>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
