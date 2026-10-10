import { PricingOverrides } from "@/components/PricingOverrides";
import { SettingsPage } from "@/components/SettingsPage";

export default function Page() {
  return (
    <SettingsPage needs="pricing.read" what="viewing prices">
      <PricingOverrides />
    </SettingsPage>
  );
}
