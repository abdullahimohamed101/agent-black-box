import type { RunStatus } from "@/lib/api/types";

/** Status is always glyph + word, never colour alone (accessibility). */
const META: Record<RunStatus, { glyph: string; label: string; tone: string }> = {
  QUEUED: { glyph: "○", label: "Queued", tone: "neutral" },
  RUNNING: { glyph: "●", label: "Running", tone: "info" },
  WAITING: { glyph: "❙❙", label: "Waiting", tone: "warn" },
  WAITING_FOR_APPROVAL: { glyph: "❙❙", label: "Awaiting approval", tone: "warn" },
  SUCCESS: { glyph: "✓", label: "Success", tone: "ok" },
  FAILED: { glyph: "✕", label: "Failed", tone: "bad" },
  CANCELLED: { glyph: "⊘", label: "Cancelled", tone: "neutral" },
  TIMED_OUT: { glyph: "⧖", label: "Timed out", tone: "bad" },
  BLOCKED: { glyph: "⊗", label: "Blocked", tone: "bad" },
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
