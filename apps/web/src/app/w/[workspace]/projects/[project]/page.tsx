import { Dashboard } from "@/components/Dashboard";

export default async function ProjectDashboard({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  return <Dashboard base={{ workspace, project }} />;
}
