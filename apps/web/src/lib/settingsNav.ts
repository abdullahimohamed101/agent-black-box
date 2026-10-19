import { settingsNav, type SettingsSlug } from "@/copy/settings";

export type SettingsTabInfo = { slug: SettingsSlug; label: string; href: string; group: string };

/** The settings tabs a person may open, in nav order. Permissions mirror the pages; the API still decides every request. */
export function settingsTabs(workspace: string, permissions: readonly string[]): SettingsTabInfo[] {
  return settingsNav.groups.flatMap((g) =>
    g.tabs
      .filter((t) => t.needs === null || permissions.includes(t.needs))
      .map((t) => ({
        slug: t.slug,
        label: t.label,
        href: `/w/${workspace}/settings/${t.slug}`,
        group: g.id,
      })),
  );
}

/** Where the Settings link in the top bar lands: the first tab this person can read (Sessions is everyone's). */
export const firstSettingsHref = (workspace: string, permissions: readonly string[]): string =>
  settingsTabs(workspace, permissions)[0]?.href ?? `/w/${workspace}/settings/sessions`;
