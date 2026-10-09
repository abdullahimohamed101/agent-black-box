"use client";

import { ApiRequestError } from "@/lib/api/client";

/** Shared pieces of the settings pages. All text from the API is rendered as text. */

export function Forbidden({ what }: { what: string }) {
  return (
    <div className="state" data-state="forbidden" role="note">
      <p className="state-title">Not available for your role</p>
      <p className="muted">Your role in this workspace does not include {what}.</p>
    </div>
  );
}

/** The outcome of a failed write: a clean banner (403 included), never a crash. */
export function ActionError({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiRequestError ? error : null;
  const denied = e?.status === 403;
  return (
    <p role="alert" className="notice" data-state={denied ? "denied" : "error"}>
      {denied ? "Your role does not allow this. " : ""}
      {e ? e.message : "The request failed."}
      {e?.requestId && <> (request {e.requestId})</>}
    </p>
  );
}

export function FieldError({ message }: { message?: string }) {
  return message ? (
    <span className="field-error" role="alert">
      {message}
    </span>
  ) : null;
}

export const when = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "—";
