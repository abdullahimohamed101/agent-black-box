import { redirect } from "next/navigation";
import { runsPath } from "@/lib/routes";

// Overview is merged into Runs (design pass 2, ADR-166): the project's home is its runs, in the same scope.
export default async function ProjectHome({
  params,
}: {
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  redirect(runsPath({ workspace, project }));
}
