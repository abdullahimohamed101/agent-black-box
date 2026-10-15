"use client";

import { useState } from "react";
import { useRevokeOtherSessions, useRevokeSession, useSessions } from "@/lib/admin";
import type { components } from "@/lib/api/schema";
import { ActionError, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import "../app/sessions.css";

type Session = components["schemas"]["SessionOut"];

/**
 * Where the signed-in person is signed in (Phase 19a, KI-068). Everyone sees their own sessions only; the API scopes
 * the list and answers the same 404 for a session that is not theirs. The session in use is marked and has no revoke
 * control (signing out is its own button); "sign out everywhere else" asks first and never touches it. Nothing shown
 * here is a credential: the handle is not the cookie value.
 */
export function SessionsSettings() {
  const sessions = useSessions();
  const revokeOne = useRevokeSession();
  const revokeOthers = useRevokeOtherSessions();
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const others = sessions.data?.filter((s) => !s.current) ?? [];
  return (
    <div className="sessions">
      <div className="page-head">
        <h1>Sessions</h1>
        <p className="muted">
          The browsers and devices where you are signed in. Ending a session signs that device out
          at once. Each change is written to the audit log of your workspaces.
        </p>
      </div>
      {sessions.isPending ? (
        <Loading label="Loading sessions" />
      ) : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : sessions.data.length === 0 ? (
        <Empty title="No sessions">
          You have no working session. Reload the page to sign in again.
        </Empty>
      ) : (
        <>
          <ul className="session-list" aria-label="Your sessions">
            {sessions.data.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                busy={revokeOne.isPending || revokeOthers.isPending}
                onRevoke={() => {
                  setNotice(null);
                  revokeOne.mutate(s.id, { onSuccess: () => setNotice("Session ended.") });
                }}
              />
            ))}
          </ul>
          {others.length > 0 && (
            <div className="session-others">
              {confirming ? (
                <div role="group" aria-label="Confirm signing out everywhere else">
                  <p>
                    Sign out{" "}
                    {others.length === 1
                      ? "the 1 other session"
                      : `${others.length} other sessions`}
                    ? This device stays signed in.
                  </p>
                  <button
                    type="button"
                    className="danger"
                    disabled={revokeOthers.isPending}
                    onClick={() => {
                      setConfirming(false);
                      setNotice(null);
                      revokeOthers.mutate(undefined, {
                        onSuccess: (n) =>
                          setNotice(
                            n === 1
                              ? "Signed out 1 other session."
                              : `Signed out ${n} other sessions.`,
                          ),
                      });
                    }}
                  >
                    Confirm sign out everywhere else
                  </button>{" "}
                  <button type="button" onClick={() => setConfirming(false)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <button type="button" className="danger" onClick={() => setConfirming(true)}>
                  Sign out everywhere else
                </button>
              )}
            </div>
          )}
        </>
      )}
      <div role="status" aria-live="polite" className="session-status" data-testid="session-status">
        {notice}
      </div>
      <ActionError error={revokeOne.error ?? revokeOthers.error} />
    </div>
  );
}

function SessionItem({
  session,
  busy,
  onRevoke,
}: {
  session: Session;
  busy: boolean;
  onRevoke: () => void;
}) {
  return (
    <li className="session-item" data-testid="session-item" data-current={session.current}>
      <div className="session-head">
        <h2>{session.device ?? "Unknown device"}</h2>
        <span className="session-status-word" data-current={session.current}>
          <span className="session-glyph" aria-hidden="true">
            {session.current ? "●" : "○"}
          </span>
          {session.current ? "This session" : "Signed in"}
        </span>
      </div>
      <dl className="session-facts">
        <div>
          <dt>Started</dt>
          <dd>{when(session.created_at)}</dd>
        </div>
        <div>
          <dt>Last used</dt>
          <dd>{when(session.last_seen_at)}</dd>
        </div>
        <div>
          <dt>Ends</dt>
          <dd>{when(session.expires_at)}</dd>
        </div>
      </dl>
      {session.current ? (
        <p className="muted session-note">To end this session, use Sign out.</p>
      ) : (
        <button
          type="button"
          disabled={busy}
          aria-label={`End session on ${session.device ?? "unknown device"}, last used ${when(session.last_seen_at)}`}
          onClick={onRevoke}
        >
          End session
        </button>
      )}
    </li>
  );
}
