import { securitySeverityMeta } from "@/lib/security";
import "../app/security.css";

/**
 * A shield: filled for the most severe, outlined for the rest. Decoration only (`aria-hidden`); the word beside it is the name.
 * The colour comes from `currentColor`, set by `security.css` from ink, amber (--wait) or muted, never red (ADR-111).
 */
export function ShieldGlyph({
  filled = false,
  className,
}: {
  filled?: boolean;
  className?: string;
}) {
  return (
    <svg
      className={className ? `sec-shield ${className}` : "sec-shield"}
      viewBox="0 0 16 16"
      width="14"
      height="14"
      aria-hidden="true"
      focusable="false"
      data-filled={filled || undefined}
    >
      <path
        d="M8 1.5 13.5 3.5v4.2c0 3.1-2.3 5.6-5.5 6.8-3.2-1.2-5.5-3.7-5.5-6.8V3.5Z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Severity as glyph and word. Critical: filled shield, ink, bold. High: amber outline. Medium and below: muted ink. */
export function SecuritySeverityMark({ severity }: { severity: string }) {
  const m = securitySeverityMeta(severity);
  return (
    <span className="sec-sev" data-severity={severity} data-tone={m.tone} data-style={m.style}>
      <ShieldGlyph filled={m.style === "filled"} />
      <span className="sec-sev-word">{m.label}</span>
    </span>
  );
}
