import type { EventOut } from "@/lib/api/types";
import { formatDuration } from "../format";
import { eventTimes } from "./times";

/**
 * Replay pacing (Phase 12, D2). Each gap between consecutive events (in canonical order) plays at its recorded length,
 * clamped to 80 ms-1.5 s at 1x; 2x and 5x divide the delay. A gap the clamp shortened is labelled, in words and without
 * any event content. Order is never the clock: a negative gap (a skewed multi-agent run, timestamps going backwards) is
 * zero, a missing or unparseable timestamp is zero.
 */

export const SPEEDS = [1, 2, 5] as const;
export type Speed = (typeof SPEEDS)[number];
export const MIN_DELAY_MS = 80;
export const MAX_DELAY_MS = 1500;

/** A recorded gap as a playback delay at the given speed. */
export function clampGap(gapMs: number, speed: number = 1): number {
  const gap = Number.isFinite(gapMs) && gapMs > 0 ? gapMs : 0;
  const s = Number.isFinite(speed) && speed > 0 ? speed : 1;
  return Math.min(MAX_DELAY_MS, Math.max(MIN_DELAY_MS, gap)) / s;
}

/** What the preceding event was, in words. Event kinds only, never attributes or payloads. */
export function kindWords(eventType: string): string {
  const family = eventType.split(".")[0] ?? "";
  switch (family) {
    case "llm":
      return "model call";
    case "tool":
      return "tool call";
    case "shell":
      return "shell command";
    case "file":
    case "git":
      return "file change";
    case "retry":
    case "timeout":
    case "rate_limit":
    case "loop":
      return "retry wait";
    case "approval":
    case "policy":
      return "approval";
    case "agent":
      return "agent step";
    case "run":
      return "run";
    default:
      return "step";
  }
}

export class ReplayPacer {
  private readonly times: Float64Array;
  constructor(private readonly events: readonly EventOut[]) {
    this.times = eventTimes(events);
  }

  /** Recorded milliseconds between event i-1 and event i; 0 for the first event, a missing time or a negative gap. */
  gapMs(i: number): number {
    if (i <= 0 || i >= this.events.length) return 0;
    const gap = this.times[i]! - this.times[i - 1]!;
    return Number.isFinite(gap) && gap > 0 ? gap : 0;
  }

  /** How long to wait before showing event i, at the given speed. Nothing precedes the first event. */
  delayBefore(i: number, speed: number = 1): number {
    return i <= 0 || i >= this.events.length ? 0 : clampGap(this.gapMs(i), speed);
  }

  /** "40 s model call, skipped to 1.5 s" when the real gap before event i exceeded the clamp, else null. */
  gapNote(i: number): string | null {
    const gap = this.gapMs(i);
    if (gap <= MAX_DELAY_MS) return null;
    const prev = this.events[i - 1]!;
    // A *.started event's gap is the call itself; after anything else it is idle time that followed it.
    const what = prev.event_type.endsWith(".started")
      ? kindWords(prev.event_type)
      : `gap after the ${kindWords(prev.event_type)}`;
    return `${formatDuration(gap)} ${what}, skipped to ${formatDuration(MAX_DELAY_MS)}`;
  }
}
