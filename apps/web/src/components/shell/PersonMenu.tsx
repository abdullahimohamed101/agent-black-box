"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { shell } from "@/copy/shell";
import { securityPath } from "@/lib/security";
import { alertsPath, type Base } from "@/lib/routes";
import { SignOutButton } from "../SignOutButton";
import { useWorkspaceOptional } from "../WorkspaceProvider";
import { useMenu } from "./useMenu";

const Person = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
    <circle cx="8" cy="5.5" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
    <path
      d="M3 13.5c.7-2.4 2.6-3.5 5-3.5s4.3 1.1 5 3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

const titleCase = (s: string) => s.charAt(0) + s.slice(1).toLowerCase();

/**
 * Who is signed in and the links that have no top-level place yet. Sessions belongs here for good. Security findings and
 * Alerts are TEMPORARY entries (design pass 2 step 5a): Review and Settings absorb them in the next batches, and until
 * then they must not be orphaned. Fixture data has no person, so the button reads "Account" and there is no sign-out.
 */
export function PersonMenu({ base }: { base: Base }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const { open, attach, toggle, onBlur } = useMenu(path);
  if (!ws) return null;
  const signedIn = ws.mode === "session" && ws.user;
  const email = signedIn ? ws.user!.email : null;
  const name = email ?? shell.person.account;
  const alerts = alertsPath(base.workspace).split("?")[0]!;
  const security = securityPath(base.workspace, base.project === "all" ? null : base.project);
  return (
    <div className="menu menu-end" ref={attach} onBlur={onBlur}>
      <button
        type="button"
        className="menu-trigger person-button"
        aria-expanded={open}
        aria-controls="person-panel"
        aria-label={name}
        onClick={toggle}
      >
        <Person />
        <span className="person-text" aria-hidden="true">
          {name}
        </span>
      </button>
      {open && (
        <div id="person-panel" className="menu-panel">
          {email && (
            <div className="menu-who">
              <p className="menu-label">{shell.person.signedInAs}</p>
              <p className="menu-email">{email}</p>
              {ws.role && <p className="menu-aside">{shell.person.role(titleCase(ws.role))}</p>}
            </div>
          )}
          <ul className="menu-list">
            {ws.permissions.includes("run.read") && (
              <li>
                <Link href={security}>{shell.person.security}</Link>
              </li>
            )}
            {ws.permissions.includes("alert.read") && (
              <li>
                <Link href={alerts}>{shell.person.alerts}</Link>
              </li>
            )}
            {signedIn && (
              <li>
                <Link href={`/w/${base.workspace}/settings/sessions`}>{shell.person.sessions}</Link>
              </li>
            )}
            {signedIn && (
              <li>
                {/* A POST: signing out is never a GET (a cross-site link must not be able to do it). */}
                <SignOutButton className="menu-action" />
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
