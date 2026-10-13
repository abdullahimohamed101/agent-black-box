import { SecuritySettings } from "@/components/SecuritySettings";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="project.write" what="security settings">
      <SecuritySettings />
    </SettingsPage>
  );
}
