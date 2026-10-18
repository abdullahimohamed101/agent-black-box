"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import { settings } from "@/copy/settings";
import "../app/settings.css";

/**
 * The pieces of THE ONE settings pattern (ADR-166, 05-microcopy-and-states.md section 8): a lede, sections with an h2 and a
 * sentence, a polite status line, one plain read-only sentence, and a two-step destructive action. All text from the API is
 * rendered as text.
 */

export function Forbidden({ what }: { what: string }) {
  return (
    <div className="state" data-state="forbidden" role="note">
      <p className="state-title">{settings.forbidden.title}</p>
      <p className="muted">{settings.forbidden.body(what)}</p>
    </div>
  );
}

/** The one sentence under the tab's name (the h1 is in the title line). At most the reading measure wide. */
export function Lede({ children }: { children: React.ReactNode }) {
  return <p className="st-lede">{children}</p>;
}

/**
 * A hairline-separated band of a tab: an h2, one sentence and the work (a table, a form). The one filled button of a
 * section goes last, inside `children`. `action` puts a control at the right end of the heading line.
 */
export function Section({
  title,
  lede,
  action,
  children,
  testId,
}: {
  title: string;
  lede?: React.ReactNode;
  action?: React.ReactNode;
  children?: React.ReactNode;
  testId?: string;
}) {
  const id = useId();
  return (
    <section className="st-section" aria-labelledby={id} data-testid={testId}>
      <div className="st-section-head">
        <h2 id={id}>{title}</h2>
        {action}
      </div>
      {lede && <p className="st-section-lede">{lede}</p>}
      {children}
    </section>
  );
}

/** A role that can read a section but not change it gets one sentence instead of a form (no disabled controls). */
export function ReadOnly({ children, testId }: { children?: React.ReactNode; testId?: string }) {
  return (
    <p className="st-readonly" role="note" data-testid={testId}>
      {children ?? settings.readOnly}
    </p>
  );
}

/** What the last save did, said politely and kept where the person's eyes already are (under the button). */
export function StatusLine({ children, testId }: { children?: React.ReactNode; testId?: string }) {
  return (
    <div className="st-status" role="status" aria-live="polite" data-testid={testId}>
      {children}
    </div>
  );
}

/**
 * A destructive action in two steps, in place (no dialog): the first press turns the button into the confirm button and a
 * Cancel; Escape and Cancel put it back and return focus. Caution text, no fill, never red (ADR-166).
 */
export function TwoStep({
  label,
  confirmLabel,
  ariaLabel,
  confirmAriaLabel,
  question,
  cancelLabel = settings.cancel,
  onConfirm,
  disabled,
}: {
  label: string;
  confirmLabel: string;
  ariaLabel?: string;
  confirmAriaLabel?: string;
  question?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  const second = useRef<HTMLButtonElement>(null);
  const back = useRef(false);
  useEffect(() => {
    if (asking) second.current?.focus();
    else if (back.current) {
      back.current = false;
      first.current?.focus();
    }
  }, [asking]);
  const cancel = () => {
    back.current = true;
    setAsking(false);
  };
  if (!asking)
    return (
      <button
        ref={first}
        type="button"
        className="danger"
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  return (
    <span
      className="st-two-step"
      role="group"
      aria-label={question ?? confirmLabel}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          cancel();
        }
      }}
    >
      {question && <span className="st-two-step-q">{question}</span>}
      <button
        ref={second}
        type="button"
        className="danger"
        aria-label={confirmAriaLabel}
        disabled={disabled}
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
      <button type="button" onClick={cancel}>
        {cancelLabel}
      </button>
    </span>
  );
}

/** The outcome of a failed write: a clean sentence (403 included), never a crash. */
export function ActionError({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiRequestError ? error : null;
  const denied = e?.status === 403;
  return (
    <p role="alert" className="notice" data-state={denied ? "denied" : "error"}>
      {denied ? `${settings.denied} ` : ""}
      {e ? e.message : settings.failed}
      {e?.requestId && <> ({settings.request(e.requestId)})</>}
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
