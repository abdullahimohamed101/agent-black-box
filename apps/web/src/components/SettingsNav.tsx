"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { settingsNav } from "@/copy/settings";
import { settingsTabs, type SettingsTabInfo } from "@/lib/settingsNav";
import { useMenu } from "./shell/useMenu";
import { useWorkspace } from "./WorkspaceProvider";
import "../app/settings.css";

export type { SettingsTabInfo };

/** The tabs this person may open, in nav order. */
export function useSettingsTabs() {
  const ws = useWorkspace();
  return settingsTabs(ws.workspace.slug, ws.permissions);
}

/**
 * The one settings navigation (design pass 2): a grouped vertical list from 900 px, and below that one button, "Section:
 * Quotas", that opens the same list under it (so nine tabs never become a sideways-scrolling row). The same links serve
 * both: there is one list in the DOM, CSS decides whether it is shown or folded.
 */
export function SettingsNav() {
  const path = usePathname();
  const tabs = useSettingsTabs();
  const { open, attach, toggle, onBlur } = useMenu(path);
  const current = tabs.find((t) => path === t.href || path.startsWith(`${t.href}/`));
  return (
    <nav className="st-nav" aria-label={settingsNav.label} ref={attach} onBlur={onBlur}>
      <button
        type="button"
        className="st-nav-toggle"
        aria-expanded={open}
        aria-controls="st-nav-list"
        onClick={toggle}
      >
        <span>
          {settingsNav.sectionPrefix}: <strong>{current?.label ?? ""}</strong>
        </span>
        <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">
          <path
            d="M3 6l5 5 5-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <div id="st-nav-list" className="st-nav-list" data-open={open ? "true" : undefined}>
        {settingsNav.groups.map((g) => {
          const items = tabs.filter((t) => t.group === g.id);
          if (items.length === 0) return null;
          return (
            <div className="st-group" key={g.id}>
              <p className="st-group-label" id={`st-group-${g.id}`}>
                {g.label}
              </p>
              <ul aria-labelledby={`st-group-${g.id}`}>
                {items.map((t) => (
                  <li key={t.slug}>
                    <Link href={t.href} aria-current={t === current ? "page" : undefined}>
                      {t.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </nav>
  );
}
