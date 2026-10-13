import { AppShell } from "@/components/AppShell";

export default async function SecurityLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  return <AppShell base={{ workspace, project: "all" }}>{children}</AppShell>;
}
