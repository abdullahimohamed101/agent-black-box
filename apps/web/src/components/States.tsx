import { ApiRequestError } from "@/lib/api/client";
import { Motif } from "./illustrations";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="state" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}…
    </div>
  );
}

/**
 * One plain sentence, the next action when there is one, and the small trace motif (Phase 22 D9). The motif is
 * decoration (`aria-hidden`); the title and sentence carry the meaning.
 */
export function Empty({
  title,
  children,
  motif = "trace",
  action,
}: {
  title: string;
  children?: React.ReactNode;
  motif?: Motif;
  action?: React.ReactNode;
}) {
  return (
    <div className="state" data-state="empty">
      <Motif kind={motif} />
      <p className="state-title">{title}</p>
      {children && <p className="muted">{children}</p>}
      {action && <div className="state-action">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiRequestError ? error : null;
  const notFound = e?.status === 404;
  return (
    <div role="alert" className="state" data-state="error">
      <Motif kind={notFound ? "tape" : "cut"} />
      <p className="state-title">{notFound ? "Not found" : "Could not load this data"}</p>
      <p className="muted">
        {e ? e.message : error instanceof Error ? error.message : "Unexpected error"}
        {e?.requestId && <> (request {e.requestId})</>}
      </p>
      {onRetry && !notFound && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
