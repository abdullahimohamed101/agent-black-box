"use client";

import { useState } from "react";

/**
 * Signs out with a same-origin `fetch`, not a form: the app sends `Referrer-Policy: no-referrer`, and browsers then
 * put `Origin: null` on a form POST, which the exact-origin rule (rightly) refuses. A fetch carries the real origin.
 */
export function SignOutButton() {
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  async function signOut() {
    setState("busy");
    try {
      const res = await fetch("/api/auth/logout", { method: "POST", redirect: "manual" });
      // `opaqueredirect` is the 303 to /login with the cookie already cleared by the browser.
      if (res.ok || res.type === "opaqueredirect") {
        // A full load on purpose: it drops every cached tenant query of the ended session.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/login");
        return;
      }
    } catch {
      /* fall through to the failure notice */
    }
    setState("failed");
  }
  return (
    <>
      <button type="button" onClick={() => void signOut()} disabled={state === "busy"}>
        Sign out
      </button>
      {state === "failed" && (
        <span role="alert" className="field-error">
          Sign-out failed; you may still be signed in.
        </span>
      )}
    </>
  );
}
