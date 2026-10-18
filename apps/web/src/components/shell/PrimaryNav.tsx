"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { shell } from "@/copy/shell";
import {
  analyticsPath,
  approvalsPath,
  experimentsPath,
  projectPath,
  runsPath,
  type Base,
} from "@/lib/routes";
import { firstSettingsHref } from "@/lib/settingsNav";
import { securityPath } from "@/lib/security";
import { ApprovalsCount } from "../ApprovalsCount";
import { useWorkspaceOptional } from "../WorkspaceProvider";
import { Brand } from "./Brand";
import { LiveIndicator } from "./LiveIndicator";
import { useMenu } from "./useMenu";

/**
 * The brand, the five primary links and (below 900 px) the Menu button that opens them as a panel. DOM order is the focus
 * order: Menu, brand, links. Review is one page with two tabs (design pass 2 step 5c): the link leads to Approvals when the
 * person may read them and to Security findings otherwise; both routes are the tabs' URLs. Settings leads to the first tab
 * the role may read.
 */
export function PrimaryNav({ base }: { base: Base }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const { open, attach, toggle, onBlur } = useMenu(path);
  const root = projectPath(base);
  const runs = runsPath(base);
  const analytics = analyticsPath(base);
  const experiments = experimentsPath(base);
  const approvals = approvalsPath(base.workspace);
  const security = securityPath(base.workspace);
  const settings = `/w/${base.workspace}/settings`;

  const canApprove = !!ws?.permissions.includes("approval.read");
  const canReview = !ws || ws.permissions.includes("run.read") || canApprove;
  const under = (href: string) => path === href || path.startsWith(`${href}/`);
  const cur = (on: boolean) => (on ? "page" : undefined);

  return (
    <div className="topbar-start" ref={attach} onBlur={onBlur}>
      <button
        type="button"
        className="menu-button"
        aria-expanded={open}
        aria-controls="primary-nav"
        onClick={toggle}
      >
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
          <path
            d="M2 4h12M2 8h12M2 12h12"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
        <span className="menu-button-text">{shell.menu}</span>
      </button>
      <Brand href={runs} />
      <nav
        id="primary-nav"
        aria-label={shell.primaryLabel}
        className="topnav"
        data-open={open ? "true" : undefined}
      >
        <ul>
          <li>
            <Link href={runs} aria-current={cur(path === root || under(runs))}>
              {shell.nav.runs}
            </Link>
            <LiveIndicator base={base} />
          </li>
          {canReview && (
            <li>
              <Link
                href={canApprove ? approvals : security}
                aria-current={cur(under(approvals) || under(security))}
              >
                {shell.nav.review}
                {canApprove && <ApprovalsCount />}
              </Link>
            </li>
          )}
          <li>
            <Link href={analytics} aria-current={cur(under(analytics))}>
              {shell.nav.analytics}
            </Link>
          </li>
          <li>
            <Link href={experiments} aria-current={cur(under(experiments))}>
              {shell.nav.experiments}
            </Link>
          </li>
          {ws?.mode === "session" && (
            <li>
              <Link
                href={firstSettingsHref(base.workspace, ws.permissions)}
                aria-current={cur(under(settings))}
              >
                {shell.nav.settings}
              </Link>
            </li>
          )}
        </ul>
      </nav>
    </div>
  );
}
