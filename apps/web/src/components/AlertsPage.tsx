"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ALERT_TABS, parseAlertTab, type AlertTab } from "@/lib/alerts";
import { AlertDestinations } from "./AlertDestinations";
import { AlertHistory } from "./AlertHistory";
import { AlertRules, type Intent } from "./AlertRules";
import { Forbidden } from "./SettingsKit";
import { useCan } from "./WorkspaceProvider";
import "../app/alerts.css";

/**
 * The workspace Alerts page. The tab is the URL's `?tab=`, so a link opens exactly this view. Owners and admins manage;
 * other roles read rules and history. Alerts are best-effort notifications with at-least-once delivery, and say so.
 */
export function AlertsPage() {
  const canRead = useCan("alert.read");
  const pathname = usePathname();
  const sp = useSearchParams();
  const tab = parseAlertTab(sp?.get("tab"));
  const [intent, setIntent] = useState<{ tab: AlertTab; value: Intent } | null>(null);

  const href = (t: AlertTab) => (t === "rules" ? pathname : `${pathname}?tab=${t}`);
  const go = (t: AlertTab, value: Intent = null) => {
    setIntent(value ? { tab: t, value } : null);
    window.history.pushState(null, "", href(t));
  };

  if (!canRead)
    return (
      <div className="al-page">
        <Head />
        <Forbidden what="alerts" />
      </div>
    );
  const mine = intent?.tab === tab ? intent.value : null;
  return (
    <div className="al-page">
      <Head />
      <nav className="al-tabs" aria-label="Alert sections">
        <ul>
          {ALERT_TABS.map((t) => (
            <li key={t.key}>
              <Link
                href={href(t.key)}
                aria-current={tab === t.key ? "page" : undefined}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                  e.preventDefault();
                  go(t.key);
                }}
              >
                {t.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {tab === "rules" && <AlertRules go={go} intent={mine} />}
      {tab === "destinations" && <AlertDestinations go={go} intent={mine} />}
      {tab === "history" && <AlertHistory />}
      <p className="muted al-foot">
        Alerts are notifications that are sent as soon as possible and may arrive more than once.
        They carry names, ids and counts, never what an agent read, wrote or ran.
      </p>
    </div>
  );
}

function Head() {
  return (
    <div className="page-head">
      <h1>Alerts</h1>
      <p className="muted">
        Tell a person or a channel when a run fails, a security finding appears, an approval needs
        attention or spend goes over a limit.
      </p>
    </div>
  );
}
