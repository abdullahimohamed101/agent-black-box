import { redirect } from "next/navigation";

export default async function SettingsHome({ params }: { params: Promise<{ workspace: string }> }) {
  const { workspace } = await params;
  redirect(`/w/${workspace}/settings/members`);
}
