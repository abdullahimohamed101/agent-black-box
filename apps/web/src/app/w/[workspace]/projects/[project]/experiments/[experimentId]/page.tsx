import { ExperimentDetail } from "@/components/ExperimentDetail";

export default async function ExperimentPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string; experimentId: string }>;
}) {
  const { workspace, project, experimentId } = await params;
  return <ExperimentDetail base={{ workspace, project }} experimentId={experimentId} />;
}
