import { Experiments } from "@/components/Experiments";

export default async function ExperimentsPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  return <Experiments base={{ workspace, project }} />;
}
