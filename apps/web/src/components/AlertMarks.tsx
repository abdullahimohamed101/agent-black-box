"use client";

import { ApiRequestError } from "@/lib/api/client";
import { problemFrom, type StatusMeta } from "@/lib/alerts";
import { ActionError } from "./SettingsKit";
import "../app/alerts.css";

/**
 * Glyph and word. Colour never carries the meaning and never means a verdict (ADR-111): trouble is amber with a
 * different glyph and word, healthy is ink, waiting and skipped are muted. Nothing here is red.
 */
export function StatusMark({ meta, ...rest }: { meta: StatusMeta } & { "data-testid"?: string }) {
  return (
    <span className="al-mark" data-tone={meta.tone} {...rest}>
      <span className="al-glyph" aria-hidden="true">
        {meta.glyph}
      </span>
      <span className="al-word">{meta.word}</span>
    </span>
  );
}

/**
 * The outcome of a failed write. The API's fixed codes get a plain sentence; anything else falls back to the shared
 * banner. Field-level problems are shown by the form next to the field, so only a banner is rendered here.
 */
export function AlertProblem({ error, kind }: { error: unknown; kind: "rule" | "destination" }) {
  if (!error) return null;
  const p = problemFrom(error, kind);
  if (p.banner)
    return (
      <p role="alert" className="notice" data-state="error">
        {p.banner}
        {error instanceof ApiRequestError && error.requestId && <> (request {error.requestId})</>}
      </p>
    );
  if (Object.keys(p.fields).length) return null;
  return <ActionError error={error} />;
}
