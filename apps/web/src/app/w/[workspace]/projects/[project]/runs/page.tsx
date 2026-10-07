import { Suspense } from "react";
import { RunsListPage } from "@/components/RunsListPage";
import { Loading } from "@/components/States";

export default async function RunsPage({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  return (
    <Suspense fallback={<Loading label="Loading runs" />}>
      <RunsListPage base={{ workspace, project }} />
    </Suspense>
  );
}
