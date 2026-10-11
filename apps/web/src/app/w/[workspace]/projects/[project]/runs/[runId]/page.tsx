import { Suspense } from "react";
import { RunDetail } from "@/components/RunDetail";
import { Loading } from "@/components/States";

export default async function RunPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string; runId: string }>;
}) {
  const { workspace, project, runId } = await params;
  // Fixture data has no live source: those deployments poll (ADR-020, ADR-022).
  const live = process.env.ABB_WEB_DATA_SOURCE !== "fixtures";
  // RunDetail reads the view from the URL (`useSearchParams`), which needs a Suspense boundary at build time.
  return (
    <Suspense fallback={<Loading label="Loading run" />}>
      <RunDetail runId={runId} base={{ workspace, project }} live={live} />
    </Suspense>
  );
}
