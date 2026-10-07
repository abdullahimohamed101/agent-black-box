export const DASH = "—";

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null || !Number.isFinite(usd)) return DASH;
  if (usd === 0) return "$0.00";
  return usd < 0.1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

export const formatInt = (n: number | null | undefined): string =>
  n == null || !Number.isFinite(n) ? DASH : n.toLocaleString("en-US");

/** Offset of an event from the run's first event, e.g. `+1.2s`; the timeline's reading order cue. */
export function formatOffset(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "+0s";
  if (ms < 1000) return `+${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `+${s.toFixed(1)}s`;
  return `+${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}s`;
}

export function formatRelative(iso: string, now: number = Date.now()): string {
  const diff = now - Date.parse(iso);
  if (!Number.isFinite(diff)) return DASH;
  const s = Math.round(diff / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export const formatTimestamp = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? DASH
    : d
        .toISOString()
        .replace("T", " ")
        .replace(/\.\d+Z$/, "Z");
};

/** Summary values are an open JSON object; read numbers defensively. */
export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
