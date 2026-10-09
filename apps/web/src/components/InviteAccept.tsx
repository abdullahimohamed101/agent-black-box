"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiRequestError, unwrap } from "@/lib/api/client";
import { forgetInviteToken, takeInviteToken } from "@/lib/invite";

type Phase =
  | { kind: "reading" }
  | { kind: "no-token" }
  | { kind: "signed-out" }
  | { kind: "ready"; email: string }
  | { kind: "unavailable" };

/** Accepts an invitation. Untrusted text (API messages, emails) is rendered as text only. */
export function InviteAccept() {
  const [phase, setPhase] = useState<Phase>({ kind: "reading" });
  const [token, setToken] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const found = takeInviteToken();
    let live = true;
    void (async () => {
      // A plain fetch: a signed-out visitor is expected here and must not be bounced by the global 401 handler.
      let next: Phase;
      try {
        const res = await fetch("/api/abb/v1/me", { cache: "no-store" });
        if (res.status === 401) next = found ? { kind: "signed-out" } : { kind: "no-token" };
        else if (!res.ok) next = { kind: "unavailable" };
        else {
          const me = (await res.json()) as { user: { email: string } };
          next = found ? { kind: "ready", email: me.user.email } : { kind: "no-token" };
        }
      } catch {
        next = { kind: "unavailable" };
      }
      if (live) {
        setToken(found);
        setPhase(next);
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  async function accept() {
    if (!token) return;
    setBusy(true);
    setFailure(null);
    try {
      const done = unwrap(await api.POST("/v1/invitations/accept", { body: { token } }));
      forgetInviteToken();
      router.push(`/w/${encodeURIComponent(done.workspace.slug)}/projects/all`);
    } catch (e) {
      if (e instanceof ApiRequestError && e.status !== 503) forgetInviteToken(); // a used or wrong link stays unusable
      setFailure(e instanceof Error ? e.message : "The invitation could not be accepted.");
      setBusy(false);
    }
  }

  if (phase.kind === "reading") return <p role="status">Checking your invitation…</p>;
  if (phase.kind === "unavailable")
    return (
      <p role="alert" className="notice">
        The service is unavailable. Reload to try again.
      </p>
    );
  if (phase.kind === "no-token")
    return (
      <p className="muted">
        This page needs an invitation link from a workspace owner. Open the full link you were sent.
      </p>
    );
  if (phase.kind === "signed-out")
    return (
      <>
        <p>Sign in with the email address the invitation was sent to, then accept it.</p>
        {/* A plain link, not next/link: prefetching it would start a sign-in. */}
        <a className="button" href={`/api/auth/login?return_to=${encodeURIComponent("/invite")}`}>
          Sign in
        </a>
      </>
    );
  return (
    <>
      <p>
        You are signed in as <strong>{phase.email}</strong>. The invitation only works for the email
        address it was sent to.
      </p>
      {failure && (
        <p role="alert" className="notice">
          {failure}
        </p>
      )}
      <button type="button" className="primary" onClick={accept} disabled={busy}>
        {busy ? "Accepting…" : "Accept invitation"}
      </button>
    </>
  );
}
