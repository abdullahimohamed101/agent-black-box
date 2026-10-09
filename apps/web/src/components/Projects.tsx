"use client";

import Link from "next/link";
import { AppShell } from "./AppShell";
import { Empty } from "./States";
import { useWorkspace } from "./WorkspaceProvider";

/** The workspace's projects, resolved on the server from `/v1/projects`. */
export function Projects({ workspace }: { workspace: string }) {
  const ws = useWorkspace();
  return (
    <AppShell base={{ workspace, project: "all" }}>
      <h1>Projects</h1>
      <p>
        <Link href={`/w/${workspace}/projects/all`}>All projects</Link>
      </p>
      {ws.projects.length === 0 ? (
        <Empty title="No projects yet">
          Projects appear once an agent reports to this workspace.
        </Empty>
      ) : (
        <ul>
          {ws.projects.map((p) => (
            <li key={p.id}>
              <Link href={`/w/${workspace}/projects/${encodeURIComponent(p.slug)}`}>{p.name}</Link>{" "}
              <span className="muted">{p.slug}</span>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
