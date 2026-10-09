import { ApiKeys } from "@/components/ApiKeys";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="api_key.read" what="managing API keys">
      <ApiKeys />
    </SettingsPage>
  );
}
