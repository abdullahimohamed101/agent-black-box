import { AppShell } from "@/components/AppShell";
import { ReviewFrame } from "@/components/ReviewFrame";

/**
 * Review is one page with two tabs (design pass 2 step 5c): Approvals at `/w/{ws}/approvals` and Security findings at
 * `/w/{ws}/security`. The route group adds no path segment; it only lets both routes share this frame, so the tab list
 * keeps focus when the person moves between them.
 */
export default async function ReviewLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  return (
    <AppShell base={{ workspace, project: "all" }}>
      <ReviewFrame>{children}</ReviewFrame>
    </AppShell>
  );
}
