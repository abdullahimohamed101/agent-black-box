"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { settings } from "@/copy/settings";
import { SettingsNav, useSettingsTabs } from "./SettingsNav";
import { TitleLine } from "./TitleLine";
import "../app/settings.css";

/**
 * THE ONE settings page (design pass 2, ADR-166): the title line "Settings / Quotas" (the h1 is the tab's name), the grouped
 * section list, and one content column no wider than the measure for prose and 60 rem for forms. Every settings tab,
 * Alerts included, renders inside it; a tab only supplies its lede and sections.
 */
/**
 * On a phone a settings table becomes one block per row (settings.css); each cell then needs its column's name in front of
 * it. The names are the table's own column heads, copied to `data-label` here, so no table repeats its heads by hand and a
 * column added later is labelled with no further change. Attributes only: this never adds or removes a node.
 */
function useCellLabels(root: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const label = () => {
      for (const table of el.querySelectorAll("table")) {
        const heads = [...table.querySelectorAll("thead th")].map(
          (th) => th.textContent?.trim() ?? "",
        );
        for (const row of table.querySelectorAll("tbody tr"))
          [...row.children].forEach((cell, i) => {
            const text = heads[i];
            if (text && cell.tagName === "TD" && cell.getAttribute("data-label") !== text)
              cell.setAttribute("data-label", text);
          });
      }
    };
    label();
    const mo = new MutationObserver(label);
    mo.observe(el, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [root]);
}

export function SettingsFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const tabs = useSettingsTabs();
  const current = tabs.find((t) => path === t.href || path.startsWith(`${t.href}/`));
  const body = useRef<HTMLDivElement>(null);
  useCellLabels(body);
  return (
    <div className="st">
      <TitleLine title={current?.label ?? settings.title} back={settings.back} />
      <div className="st-layout">
        <SettingsNav />
        <div className="st-body" ref={body}>
          {children}
        </div>
      </div>
    </div>
  );
}
