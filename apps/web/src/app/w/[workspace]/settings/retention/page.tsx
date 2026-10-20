import { RetentionSettings } from "@/components/RetentionSettings";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="retention.read" what="retention settings">
      <RetentionSettings />
    </SettingsPage>
  );
}
