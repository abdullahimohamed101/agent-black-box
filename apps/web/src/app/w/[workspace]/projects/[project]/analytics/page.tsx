import { Analytics } from "@/components/Analytics";

export default async function ProjectAnalytics({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  return <Analytics base={{ workspace, project }} />;
}
