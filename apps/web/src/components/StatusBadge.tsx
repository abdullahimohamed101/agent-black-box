import type { RunStatus } from "@/lib/api/types";

/**
 * Status is always glyph + word, never colour alone (ADR-166 keeps the ADR-111 grammar and drops the fill). The glyph is
 * decoration; the word is the name. Tones: `bad` is the one red word (failed, timed out, blocked), `warn` is caution text,
 * `info` an accent dot with the word in ink, `ok` and `neutral` ink-2 (success is a check, not green).
 */
const META: Record<RunStatus, { glyph: string; label: string; tone: string }> = {
  QUEUED: { glyph: "◌", label: "Queued", tone: "neutral" },
  RUNNING: { glyph: "●", label: "Running", tone: "info" },
  WAITING: { glyph: "‖", label: "Waiting", tone: "warn" },
  WAITING_FOR_APPROVAL: { glyph: "‖", label: "Awaiting approval", tone: "warn" },
  SUCCESS: { glyph: "✓", label: "Success", tone: "ok" },
  FAILED: { glyph: "✕", label: "Failed", tone: "bad" },
  CANCELLED: { glyph: "–", label: "Cancelled", tone: "neutral" },
  TIMED_OUT: { glyph: "✕", label: "Timed out", tone: "bad" },
  BLOCKED: { glyph: "⊘", label: "Blocked", tone: "bad" },
};

export const statusLabel = (s: RunStatus): string => META[s]?.label ?? s;

export function StatusBadge({ status }: { status: RunStatus }) {
  const m = META[status] ?? { glyph: "?", label: String(status), tone: "neutral" };
  return (
    <span className="badge" data-tone={m.tone} data-status={status}>
      <span aria-hidden="true">{m.glyph}</span> {m.label}
    </span>
  );
}
