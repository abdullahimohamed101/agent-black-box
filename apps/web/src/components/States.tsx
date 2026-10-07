import { ApiRequestError } from "@/lib/api/client";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="state" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}…
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="state" data-state="empty">
      <p className="state-title">{title}</p>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiRequestError ? error : null;
  const notFound = e?.status === 404;
  return (
    <div role="alert" className="state" data-state="error">
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
