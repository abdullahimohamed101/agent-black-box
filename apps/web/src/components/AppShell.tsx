"use client";

import { shell } from "@/copy/shell";
import type { Base } from "@/lib/routes";
import { PersonMenu } from "./shell/PersonMenu";
import { PrimaryNav } from "./shell/PrimaryNav";
import { ScopeControl } from "./shell/ScopeControl";
import { TopbarSearch } from "./shell/TopbarSearch";

/**
 * The application frame (design pass 2, ADR-166): one 44 px top bar, then the page. No rail, no second band. DOM order is
 * the focus order: skip link, Menu / brand / primary links, scope, search, person, main. "Where am I" is the page's own
 * title line, not a breadcrumb.
 */
export function AppShell({ base, children }: { base: Base; children: React.ReactNode }) {
  return (
    <div className="shell">
      <a href="#main" className="skip">
        {shell.skip}
      </a>
      <header className="topbar">
        <PrimaryNav base={base} />
        <div className="topbar-end">
          <ScopeControl base={base} />
          <TopbarSearch base={base} />
          <PersonMenu base={base} />
        </div>
      </header>
      <main id="main" tabIndex={-1} className="page">
        {children}
      </main>
    </div>
  );
}
