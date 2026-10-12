"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRun } from "@/lib/queries";
import { projectPath, runsPath, experimentsPath, type Base } from "@/lib/routes";

type Crumb = { label: string; href?: string };

const SETTINGS: Record<string, string> = {
  members: "Members",
  "api-keys": "API keys",
  pricing: "Pricing",
  audit: "Audit log",
};

/** The run's name from the query cache the run page already fills; the short id until it loads. A text node, always. */
function RunName({ runId }: { runId: string }) {
  const run = useRun(runId);
  return <>{run.data?.name ?? runId.slice(-8)}</>;
}

/** "Runs / Ship feature" from the pathname alone: no extra request except the shared run query on a run page. */
export function Crumbs({ base }: { base: Base }) {
  const path = usePathname();
  const root = projectPath(base);
  const settings = `/w/${base.workspace}/settings`;
  let trail: Crumb[];
  let runId: string | null = null;
  if (path.startsWith(settings)) {
    const leaf = SETTINGS[path.slice(settings.length + 1).split("/")[0] ?? ""];
    trail = [{ label: "Settings" }, ...(leaf ? [{ label: leaf }] : [])];
  } else if (path === root || !path.startsWith(root)) {
    trail = [{ label: "Overview" }];
  } else {
    const rest = path.slice(root.length + 1).split("/");
    const [section, id] = rest;
    if (section === "runs") {
      const runs = { label: "Runs", href: runsPath(base) };
      if (!id) trail = [{ label: "Runs" }];
      else if (id === "compare") trail = [runs, { label: "Compare" }];
      else {
        runId = id;
        trail = [runs, { label: "" }];
      }
    } else if (section === "analytics") trail = [{ label: "Analytics" }];
    else if (section === "experiments") {
      const list = { label: "Experiments", href: experimentsPath(base) };
      trail = id ? [list, { label: "Experiment" }] : [{ label: "Experiments" }];
    } else trail = [{ label: "Overview" }];
  }
  return (
    <nav className="topbar-crumbs" aria-label="Breadcrumb">
      <ol>
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          return (
            <li key={i} aria-current={last ? "page" : undefined}>
              {c.href && !last ? (
                <Link href={c.href}>{c.label}</Link>
              ) : last && runId ? (
                <RunName runId={runId} />
              ) : (
                c.label
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
