import { notFound } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { loadWorkspace } from "@/server/session";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  // Slugs resolve through the API's project list (KI-027); an unknown one is a 404, not an empty page.
  const loaded = await loadWorkspace(workspace);
  if (
    loaded.status === "ok" &&
    project !== "all" &&
    !loaded.value.projects.some((p) => p.slug === project || p.id === project)
  ) {
    notFound();
  }
  return <AppShell base={{ workspace, project }}>{children}</AppShell>;
}
