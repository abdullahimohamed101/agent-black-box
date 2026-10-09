import { AuditLog } from "@/components/AuditLog";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="audit.read" what="the audit log">
      <AuditLog />
    </SettingsPage>
  );
}
