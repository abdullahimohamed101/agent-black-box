import { notFound, redirect } from "next/navigation";
import { auth } from "@/copy/auth";
import "../../login.css";
import { Brand } from "@/components/shell/Brand";
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
    const back = safeReturnTo(`/w/${workspace}/projects/all/runs`);
    redirect(back ? `/login?return_to=${encodeURIComponent(back)}` : "/login");
  }
  if (loaded.status === "not_found") notFound(); // not a member looks like a workspace that does not exist
  if (loaded.status === "unavailable") {
    return (
      <main id="main" className="auth-page">
        <Brand />
        <h1>{auth.workspaceUnavailable.title}</h1>
        <p role="alert" className="notice">
          {loaded.message} {auth.workspaceUnavailable.suffix}
        </p>
      </main>
    );
  }
  return <WorkspaceProvider value={loaded.value}>{children}</WorkspaceProvider>;
}
