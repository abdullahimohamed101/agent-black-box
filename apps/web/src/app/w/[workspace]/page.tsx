import { redirect } from "next/navigation";

// The project list page is gone (design pass 2, ADR-166): the scope control lists projects and Runs is the home. The route
// stays so old links and bookmarks keep working.
export default async function WorkspaceHome({
  params,
}: {
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  redirect(`/w/${workspace}/projects/all/runs`);
}
