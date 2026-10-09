import { AppShell } from "@/components/AppShell";
import { SettingsNav } from "@/components/SettingsNav";

export default async function SettingsLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspace: string }>;
}) {
  const { workspace } = await params;
  return (
    <AppShell base={{ workspace, project: "all" }}>
      <SettingsNav />
      {children}
    </AppShell>
  );
}
