import { ApiRequestError } from "@/lib/api/client";
import { states } from "@/copy/states";
import { Motif } from "./illustrations";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="state" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {states.loading(label)}
    </div>
  );
}

/**
 * One plain sentence and the next action when there is one. `motif` draws the small trace motif (Phase 22 D9); ADR-166
 * keeps drawings for the first-run guide and not-found only, so a screen that has been rebuilt passes `motif="none"` and
 * the remaining screens drop theirs as their batch lands. The motif is decoration (`aria-hidden`).
 */
export function Empty({
  title,
  children,
  motif = "trace",
  action,
}: {
  title: string;
  children?: React.ReactNode;
  motif?: Motif | "none";
  action?: React.ReactNode;
}) {
  return (
    <div className="state" data-state="empty">
      {motif !== "none" && <Motif kind={motif} />}
      <p className="state-title">{title}</p>
      {children && <p className="muted">{children}</p>}
      {action && <div className="state-action">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiRequestError ? error : null;
  const notFound = e?.status === 404;
  // Retention (ADR-160): a conflict that is not a failure (the trace is gone, the run summary is not), said in words.
  if (e?.code === "TRACE_EXPIRED")
    return (
      <div className="state" data-state="empty" role="note" data-testid="trace-expired">
        <Motif kind="tape" />
        <p className="state-title">Trace expired</p>
        <p className="muted">
          The trace of this run was removed by the retention policy; the run summary and its numbers
          remain. A comparison or an evaluation needs the trace.
        </p>
      </div>
    );
  // Signed out (a 401 that reached a screen before the client's redirect did): say so and offer the way back in.
  if (e?.status === 401)
    return (
      <div role="alert" className="state" data-state="error">
        <p className="state-title">{states.error.signedOut.title}</p>
        <p className="muted">{states.error.signedOut.body}</p>
        <p className="state-action">
          <a className="button" href="/login">
            {states.error.signedOut.action}
          </a>
        </p>
      </div>
    );
  // A run that is gone: the API does not say whether retention removed it or it never existed, and neither do we.
  const message =
    e?.code === "RUN_NOT_FOUND" && notFound
      ? states.error.runGone
      : e
        ? e.message
        : error instanceof Error
          ? error.message
          : states.error.unexpected;
  const forbidden = e?.status === 403;
  const limited = e?.status === 429;
  const title = notFound
    ? states.error.notFound
    : forbidden
      ? states.error.forbidden.title
      : limited
        ? states.error.rateLimited.title
        : states.error.generic;
  return (
    <div role="alert" className="state" data-state="error">
      {notFound && <Motif kind="tape" />}
      <p className="state-title">{title}</p>
      <p className="muted">
        {limited
          ? e!.retryAfter !== null
            ? states.error.rateLimited.after(e!.retryAfter)
            : states.error.rateLimited.soon
          : message}
        {e?.requestId && <> ({states.error.request(e.requestId)})</>}
      </p>
      {onRetry && !notFound && !forbidden && (
        <button type="button" onClick={onRetry}>
          {states.error.tryAgain}
        </button>
      )}
    </div>
  );
}
