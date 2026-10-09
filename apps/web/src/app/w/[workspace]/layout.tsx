import { notFound, redirect } from "next/navigation";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { safeReturnTo } from "@/server/config";
import { loadWorkspace } from "@/server/session";

export const dynamic = "force-dynamic";

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  const loaded = await loadWorkspace(workspace);
  if (loaded.status === "unauthenticated") {
    const back = safeReturnTo(`/w/${workspace}/projects/all`);
    redirect(back ? `/login?return_to=${encodeURIComponent(back)}` : "/login");
  }
  if (loaded.status === "not_found") notFound(); // not a member looks like a workspace that does not exist
  if (loaded.status === "unavailable") {
    return (
      <main>
        <h1>Workspace unavailable</h1>
        <p role="alert" className="notice">
          {loaded.message} Reload to try again.
        </p>
      </main>
    );
  }
  return <WorkspaceProvider value={loaded.value}>{children}</WorkspaceProvider>;
}
