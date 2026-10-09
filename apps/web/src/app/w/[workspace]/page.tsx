import { Projects } from "@/components/Projects";

export default async function WorkspaceHome({
  params,
}: {
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  return <Projects workspace={workspace} />;
}
