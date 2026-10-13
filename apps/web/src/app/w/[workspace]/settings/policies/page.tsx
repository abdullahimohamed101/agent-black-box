import { PoliciesSettings } from "@/components/PoliciesSettings";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="policy.read" what="policies">
      <PoliciesSettings />
    </SettingsPage>
  );
}
