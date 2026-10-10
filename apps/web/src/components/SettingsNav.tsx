"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useWorkspace } from "./WorkspaceProvider";

/** Tabs shown by the permission each page needs; the API still decides what a request may do. */
const TABS = [
  { slug: "members", label: "Members", needs: "member.read" },
  { slug: "api-keys", label: "API keys", needs: "api_key.read" },
  { slug: "pricing", label: "Pricing", needs: "pricing.read" },
  { slug: "audit", label: "Audit log", needs: "audit.read" },
] as const;

export function SettingsNav() {
  const ws = useWorkspace();
  const path = usePathname();
  return (
    <nav className="subnav" aria-label="Settings">
      {TABS.filter((t) => ws.permissions.includes(t.needs)).map((t) => {
        const href = `/w/${ws.workspace.slug}/settings/${t.slug}`;
        return (
          <Link key={t.slug} href={href} aria-current={path === href ? "page" : undefined}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
