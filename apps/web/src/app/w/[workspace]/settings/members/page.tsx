import { Members } from "@/components/Members";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="member.read" what="viewing members">
      <Members />
    </SettingsPage>
  );
}
