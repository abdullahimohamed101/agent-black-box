"use client";

import { useMemo } from "react";
import type { EventOut } from "@/lib/api/types";
import { buildStory, isCodingRun } from "@/lib/coding";
import { run } from "@/copy/run";

const GLYPH = { ok: "✓", bad: "✕", warn: "↻", neutral: "•" } as const;

/**
 * The Steps column: what the agent read, asked the model, edited, tested and retried, numbered. Each step opens its
 * event. Failures and retries are spelled out in words and glyphs, never colour alone (and never red: the fault line is
 * the only red mark on the page).
 */
export function CodingSummary({
  events,
  onOpen,
  hideHeading = false,
}: {
  events: readonly EventOut[];
  onOpen: (eventId: string) => void;
  /** In the narrow strip the disclosure's summary is the label. */
  hideHeading?: boolean;
}) {
  const steps = useMemo(() => buildStory(events), [events]);
  if (!isCodingRun(events) || steps.length === 0) return null;
  return (
    <section aria-labelledby="story-h" className="story" data-testid="story">
      <h2 id="story-h" className={hideHeading ? "sr-only" : undefined}>
        {run.steps.heading}
      </h2>
      <ol>
        {steps.map((s) => (
          <li key={s.key} data-kind={s.kind} data-tone={s.tone}>
            <button type="button" onClick={() => onOpen(s.eventId)}>
              <span className="story-glyph" aria-hidden="true">
                {GLYPH[s.tone]}
              </span>
              <span className="story-label">{s.label}</span>
              {s.detail && <span className="story-detail">{s.detail}</span>}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
