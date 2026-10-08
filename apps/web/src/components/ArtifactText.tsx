"use client";

import { useState } from "react";
import { formatInt } from "@/lib/format";
import { useArtifactText } from "@/lib/queries";
import { DiffView } from "./DiffView";
import { ErrorState, Loading } from "./States";
import { ApiRequestError } from "@/lib/api/client";

/**
 * Text kept outside the event (terminal output, a diff), fetched only when the reader asks (`defaultOpen` for
 * diffs, which are the point of a file event). Chunks of 64 KiB, "Load more" for the rest. Always rendered as text.
 */
export function ArtifactText({
  id,
  label,
  bytes,
  mode = "plain",
  lang,
  defaultOpen = false,
}: {
  id: string | null;
  label: string;
  /** Size announced by the event, shown before anything is fetched. */
  bytes?: number | null;
  mode?: "plain" | "diff";
  lang?: string | null;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const q = useArtifactText(id, open);
  const pages = q.data?.pages ?? [];
  const text = pages.map((p) => p.content).join("");
  const total = pages[0]?.total_bytes ?? bytes ?? null;
  const loaded = pages.length ? (pages[pages.length - 1]!.next_offset ?? total) : 0;

  if (!id) {
    return (
      <p className="muted" data-testid={`artifact-${label}`}>
        {label}: not captured.
      </p>
    );
  }
  return (
    <div className="artifact" data-testid={`artifact-${label}`}>
      <button
        type="button"
        aria-expanded={open}
        className="artifact-toggle"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Hide" : "Show"} {label}
        {total != null && <span className="muted"> ({formatInt(total)} bytes)</span>}
      </button>
      {open &&
        (q.isPending ? (
          <Loading label={`Loading ${label}`} />
        ) : q.isError ? (
          q.error instanceof ApiRequestError && q.error.status === 404 ? (
            <p className="muted">
              Not available: it was not uploaded, was withheld, or has been removed.
            </p>
          ) : (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          )
        ) : (
          <>
            {mode === "diff" ? (
              <DiffView text={text} lang={lang} />
            ) : (
              // Terminal output is untrusted text: a <pre> text node, never markup.
              <pre tabIndex={0} aria-label={label} className="term">
                {text || "(empty)"}
              </pre>
            )}
            {q.hasNextPage && (
              <p>
                <button
                  type="button"
                  disabled={q.isFetchingNextPage}
                  onClick={() => void q.fetchNextPage()}
                >
                  {q.isFetchingNextPage ? "Loading…" : "Load more"}
                </button>{" "}
                <span className="muted" data-testid="artifact-progress">
                  {formatInt(loaded)} of {formatInt(total)} bytes loaded
                </span>
              </p>
            )}
          </>
        ))}
    </div>
  );
}
