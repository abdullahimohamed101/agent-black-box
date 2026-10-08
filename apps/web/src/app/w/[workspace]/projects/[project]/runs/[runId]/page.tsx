import { RunDetail } from "@/components/RunDetail";

export default async function RunPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string; runId: string }>;
}) {
  const { workspace, project, runId } = await params;
  // Fixture data has no live source: those deployments poll (ADR-020, ADR-022).
  const live = process.env.ABB_WEB_DATA_SOURCE !== "fixtures";
  return <RunDetail runId={runId} base={{ workspace, project }} live={live} />;
}
