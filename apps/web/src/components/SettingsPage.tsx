"use client";

import { Forbidden } from "./SettingsKit";
import { useCan } from "./WorkspaceProvider";

/** Renders a settings page only when the API listed its permission for this person; otherwise says so. */
export function SettingsPage({
  needs,
  what,
  children,
}: {
  needs: string;
  what: string;
  children: React.ReactNode;
}) {
  const allowed = useCan(needs);
  return allowed ? <>{children}</> : <Forbidden what={what} />;
}
