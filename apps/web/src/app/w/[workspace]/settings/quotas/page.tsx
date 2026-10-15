import { QuotasSettings } from "@/components/QuotasSettings";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="project.read" what="project quotas">
      <QuotasSettings />
    </SettingsPage>
  );
}
