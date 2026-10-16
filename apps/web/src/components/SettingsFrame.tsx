"use client";

import { usePathname } from "next/navigation";
import { settings } from "@/copy/settings";
import { SettingsNav, useSettingsTabs } from "./SettingsNav";
import { TitleLine } from "./TitleLine";
import "../app/settings.css";

/**
 * THE ONE settings page (design pass 2, ADR-166): the title line "Settings / Quotas" (the h1 is the tab's name), the grouped
 * section list, and one content column no wider than the measure for prose and 60 rem for forms. Every settings tab,
 * Alerts included, renders inside it; a tab only supplies its lede and sections.
 */
export function SettingsFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const tabs = useSettingsTabs();
  const current = tabs.find((t) => path === t.href || path.startsWith(`${t.href}/`));
  return (
    <div className="st">
      <TitleLine
        href={tabs[0]?.href ?? "#"}
        title={current?.label ?? settings.title}
        back={settings.back}
      />
      <div className="st-layout">
        <SettingsNav />
        <div className="st-body">{children}</div>
      </div>
    </div>
  );
}
