import { RunDetail } from "@/components/RunDetail";

export default async function RunPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string; runId: string }>;
}) {
  const { workspace, project, runId } = await params;
  return <RunDetail runId={runId} base={{ workspace, project }} />;
}
