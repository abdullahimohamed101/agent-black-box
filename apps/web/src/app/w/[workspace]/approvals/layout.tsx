import { AppShell } from "@/components/AppShell";

export default async function ApprovalsLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  return <AppShell base={{ workspace, project: "all" }}>{children}</AppShell>;
}
