"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";
import { settings } from "@/copy/settings";
import { ALERT_TABS, parseAlertTab, type AlertTab } from "@/lib/alerts";
import { AlertDestinations } from "./AlertDestinations";
import { AlertHistory } from "./AlertHistory";
import { AlertRules, type Intent } from "./AlertRules";
import { Forbidden, Lede } from "./SettingsKit";
import { Tabs, tabIds } from "./Tabs";
import { useCan } from "./WorkspaceProvider";
import "../app/alerts.css";

/**
 * The Alerts tab of Settings. Rules, Destinations and History are tabs of the shared component, and the tab is the URL's
 * `?tab=`, so a link opens exactly this view. Owners and admins manage; other roles read rules and history. Alerts are
 * best-effort notifications with at-least-once delivery, and say so.
 */
export function AlertsPage() {
  const canRead = useCan("alert.read");
  const pathname = usePathname();
  const sp = useSearchParams();
  const tab = parseAlertTab(sp?.get("tab"));
  const [intent, setIntent] = useState<{ tab: AlertTab; value: Intent } | null>(null);
  const c = settings.alerts;

  const href = (t: AlertTab) => (t === "rules" ? pathname : `${pathname}?tab=${t}`);
  const go = (t: AlertTab, value: Intent = null) => {
    setIntent(value ? { tab: t, value } : null);
    window.history.pushState(null, "", href(t));
  };

  if (!canRead)
    return (
      <div className="al-page">
        <Lede>{c.lede}</Lede>
        <Forbidden what="alerts" />
      </div>
    );
  const mine = intent?.tab === tab ? intent.value : null;
  return (
    <div className="al-page">
      <Lede>{c.lede}</Lede>
      <Tabs
        label={c.tabsLabel}
        idBase="alerts"
        className="tabs-ruled"
        items={ALERT_TABS.map((t) => ({ id: t.key, label: c.tabs[t.key] }))}
        value={tab}
        onChange={(t) => go(t)}
      />
      <div role="tabpanel" className="al-panel" {...tabIds("alerts", tab)}>
        {tab === "rules" && <AlertRules go={go} intent={mine} />}
        {tab === "destinations" && <AlertDestinations go={go} intent={mine} />}
        {tab === "history" && <AlertHistory />}
      </div>
      <p className="muted al-foot">{c.foot}</p>
    </div>
  );
}
