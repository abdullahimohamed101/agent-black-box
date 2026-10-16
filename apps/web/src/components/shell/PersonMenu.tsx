"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { shell } from "@/copy/shell";
import type { Base } from "@/lib/routes";
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
 * Who is signed in, their sessions and Sign out. (Security findings and Alerts were temporary entries here in step 5a;
 * Review and Settings absorbed them in 5c, KI-279.) Fixture data has no person, so the button reads "Account" and there is
 * no sign-out.
 */
export function PersonMenu({ base }: { base: Base }) {
  const path = usePathname();
  const ws = useWorkspaceOptional();
  const { open, attach, toggle, onBlur } = useMenu(path);
  if (!ws) return null;
  const signedIn = ws.mode === "session" && ws.user;
  const email = signedIn ? ws.user!.email : null;
  const name = email ?? shell.person.account;
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
          {!signedIn && <p className="menu-note">{shell.person.none}</p>}
          {signedIn && (
            <ul className="menu-list">
              <li>
                <Link href={`/w/${base.workspace}/settings/sessions`}>{shell.person.sessions}</Link>
              </li>
              <li>
                {/* A POST: signing out is never a GET (a cross-site link must not be able to do it). */}
                <SignOutButton className="menu-action" />
              </li>
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
