import { AppShell } from "@/components/AppShell";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string; project: string }>;
}) {
  const { workspace, project } = await params;
  return <AppShell base={{ workspace, project }}>{children}</AppShell>;
}
