"use client";

import { useMemo } from "react";
import type { EventOut } from "@/lib/api/types";
import { buildStory, isCodingRun } from "@/lib/coding";

const GLYPH = { ok: "✓", bad: "✕", warn: "↻", neutral: "•" } as const;

/**
 * The run as a story: what the agent read, asked the model, edited, tested, retried. Each step opens its event.
 * Failures and retries are spelled out in words and glyphs, never colour alone.
 */
export function CodingSummary({
  events,
  onOpen,
}: {
  events: readonly EventOut[];
  onOpen: (eventId: string) => void;
}) {
  const steps = useMemo(() => buildStory(events), [events]);
  if (!isCodingRun(events) || steps.length === 0) return null;
  return (
    <section aria-labelledby="story-h" className="story" data-testid="story">
      <h2 id="story-h">What the agent did</h2>
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
