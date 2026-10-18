import { AppShell } from "@/components/AppShell";
import { SettingsFrame } from "@/components/SettingsFrame";

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
      <SettingsFrame>{children}</SettingsFrame>
    </AppShell>
  );
}
