import { Suspense } from "react";
import { CompareView } from "@/components/CompareView";
import { Loading } from "@/components/States";

// A static segment: it takes precedence over `[runId]`, so `/runs/compare` is this page.
export default async function ComparePage({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  // CompareView reads the two run ids from the URL (`useSearchParams`), which needs a Suspense boundary at build time.
  return (
    <Suspense fallback={<Loading label="Loading comparison" />}>
      <CompareView base={{ workspace, project }} />
    </Suspense>
  );
}
