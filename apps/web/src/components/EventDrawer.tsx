"use client";

import { useEffect, useRef } from "react";
import type { EventOut } from "@/lib/api/types";
import { formatCost, formatDuration, formatInt, formatTimestamp, num } from "@/lib/format";
import { useEventDetail } from "@/lib/queries";
import { eventClass, isError } from "@/lib/timeline";
import { CodingSections } from "./CodingSections";
import { ErrorState, Loading } from "./States";

const PAYLOAD_CHARS = 20_000;

export type DrawerKind = "llm" | "tool" | "generic";
export const drawerKind = (e: EventOut): DrawerKind => {
  const c = eventClass(e.event_type);
  return c === "llm" ? "llm" : c === "tool" ? "tool" : "generic";
};

const ATTR_CHARS = 2000;
const clip = (v: string): string =>
  v.length > ATTR_CHARS ? `${v.slice(0, ATTR_CHARS)}… (truncated)` : v;

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function Fields({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="fields">
      {rows
        .filter(([, v]) => v != null && v !== "")
        .map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
    </dl>
  );
}

function errorMessage(
  e: EventOut,
  payload: Record<string, unknown> | null | undefined,
): string | null {
  const nested = payload?.["error"];
  const fromPayload =
    nested && typeof nested === "object"
      ? str((nested as Record<string, unknown>)["message"])
      : null;
  return (
    fromPayload ??
    str(e.attributes["error.summary"]) ??
    str(e.attributes["tool.error_type"]) ??
    str(e.attributes["llm.error_type"])
  );
}

export function EventDrawer({
  runId,
  event,
  onClose,
}: {
  runId: string;
  event: EventOut;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const detail = useEventDetail(runId, event.event_id);
  useEffect(() => closeRef.current?.focus(), [event.event_id]);

  const a = event.attributes;
  const kind = drawerKind(event);
  const err = isError(event);
  const payload = detail.data?.payload ?? null;
  const json = payload ? JSON.stringify(payload, null, 2) : null;
  const msg = err ? errorMessage(event, payload) : null;

  return (
    <aside
      className="drawer"
      role="dialog"
      aria-modal="false"
      aria-labelledby="drawer-title"
      data-kind={kind}
      data-error={err || undefined}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="drawer-head">
        <h2 id="drawer-title">{event.event_type}</h2>
        <button ref={closeRef} type="button" onClick={onClose} aria-label="Close details">
          Close
        </button>
      </div>
      <p className="muted">
        {formatTimestamp(event.occurred_at)} · #{event.sequence ?? "—"} · {event.agent_id}
      </p>

      {err && (
        <section aria-labelledby="d-err" className="drawer-error">
          <h3 id="d-err">✕ Error</h3>
          <p>{msg ?? "This event reports a failure without a message."}</p>
          <Fields
            rows={[
              ["Status", event.status ?? "failed"],
              ["Type", str(a["tool.error_type"]) ?? str(a["llm.error_type"])],
            ]}
          />
        </section>
      )}

      {kind === "llm" && (
        <section aria-labelledby="d-llm">
          <h3 id="d-llm">Model call</h3>
          <Fields
            rows={[
              ["Model", str(a["llm.model"])],
              ["Provider", str(a["llm.provider"])],
              [
                "Input tokens",
                num(a["llm.input_tokens"]) != null ? formatInt(num(a["llm.input_tokens"])) : null,
              ],
              [
                "Output tokens",
                num(a["llm.output_tokens"]) != null ? formatInt(num(a["llm.output_tokens"])) : null,
              ],
              [
                "Cached input",
                num(a["llm.cached_input_tokens"]) != null
                  ? formatInt(num(a["llm.cached_input_tokens"]))
                  : null,
              ],
              [
                "Cost (estimated)",
                num(a["cost.estimated_usd"]) != null
                  ? formatCost(num(a["cost.estimated_usd"]))
                  : null,
              ],
              ["Duration", event.duration_ms != null ? formatDuration(event.duration_ms) : null],
            ]}
          />
        </section>
      )}

      {kind === "tool" && (
        <section aria-labelledby="d-tool">
          <h3 id="d-tool">Tool call</h3>
          <Fields
            rows={[
              ["Tool", str(a["tool.name"])],
              ["Operation", str(a["tool.operation"])],
              [
                "Results",
                num(a["tool.result_count"]) != null ? formatInt(num(a["tool.result_count"])) : null,
              ],
              ["Duration", event.duration_ms != null ? formatDuration(event.duration_ms) : null],
            ]}
          />
        </section>
      )}

      <CodingSections event={event} />

      <section aria-labelledby="d-attrs">
        <h3 id="d-attrs">Attributes</h3>
        {Object.keys(a).length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <Fields
            rows={Object.entries(a)
              .sort(([x], [y]) => x.localeCompare(y))
              .map(
                ([k, v]) =>
                  [k, clip(typeof v === "string" ? v : JSON.stringify(v))] as [string, string],
              )}
          />
        )}
      </section>

      <section aria-labelledby="d-ids">
        <h3 id="d-ids">Identifiers</h3>
        <Fields
          rows={[
            ["Event", event.event_id],
            ["Span", event.span_id],
            ["Parent span", event.parent_span_id],
            ["Trace", event.trace_id],
          ]}
        />
      </section>

      <section aria-labelledby="d-payload">
        <h3 id="d-payload">Payload</h3>
        {!event.has_payload ? (
          <p className="muted">No payload was captured for this event.</p>
        ) : detail.isPending ? (
          <Loading label="Loading payload" />
        ) : detail.isError ? (
          <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
        ) : detail.data?.payload_withheld ? (
          <p className="withheld" data-testid="payload-withheld">
            Content hidden by your role. You can see that this event has a payload, not what it
            says.
          </p>
        ) : json ? (
          <>
            {/* Captured payloads are untrusted: rendered as text only. */}
            <pre tabIndex={0} aria-label="Event payload">
              {json.length > PAYLOAD_CHARS ? json.slice(0, PAYLOAD_CHARS) : json}
            </pre>
            {json.length > PAYLOAD_CHARS && (
              <p className="muted">
                Truncated: showing the first {formatInt(PAYLOAD_CHARS)} of {formatInt(json.length)}{" "}
                characters.
              </p>
            )}
          </>
        ) : (
          <p className="muted">Payload is stored externally or was redacted.</p>
        )}
      </section>
    </aside>
  );
}
