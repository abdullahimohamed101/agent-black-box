import "../../app/illustrations.css";

/**
 * The small trace system (Phase 22 D9): three line drawings used only on empty, error and not-found states.
 * Each is one `currentColor` stroke plus one accent stroke (`--illus-accent`, the interaction blue), so both themes
 * follow the tokens. They are decoration: `aria-hidden`, never the only carrier of meaning.
 */
type Props = { className?: string };

function Svg({ className, children }: Props & { children: React.ReactNode }) {
  return (
    <svg
      className={className ? `illus ${className}` : "illus"}
      viewBox="0 0 120 40"
      width="120"
      height="40"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** A run waiting to happen: a trace with one spike, and the recording head at its end. */
export function TraceLine({ className }: Props) {
  return (
    <Svg className={className}>
      <path d="M3 24h26l6-15 8 27 7-18 5 6h56" />
      <circle className="illus-accent illus-fill" cx="108" cy="24" r="3.5" />
    </Svg>
  );
}

/** The trace stops at a slanted cut and carries on as a ghost: something broke, or nothing matched. */
export function FaultCut({ className }: Props) {
  return (
    <Svg className={className}>
      <path d="M3 24h26l6-15 8 27 7-18h8" />
      <path className="illus-accent" d="M68 6l-6 28" />
      <path className="illus-ghost" d="M76 24h41" strokeDasharray="2 6" />
    </Svg>
  );
}

/** Recorder tape: two reels joined by a tape with one marker, for lists with nothing recorded yet. */
export function RecorderTape({ className }: Props) {
  return (
    <Svg className={className}>
      <circle cx="26" cy="20" r="11" />
      <circle cx="94" cy="20" r="11" />
      <circle cx="26" cy="20" r="2.5" />
      <circle cx="94" cy="20" r="2.5" />
      <path d="M37 20h46" />
      <path className="illus-accent" d="M60 13v14" />
    </Svg>
  );
}

export type Motif = "trace" | "cut" | "tape";
export function Motif({ kind, className }: { kind: Motif; className?: string }) {
  return kind === "cut" ? (
    <FaultCut className={className} />
  ) : kind === "tape" ? (
    <RecorderTape className={className} />
  ) : (
    <TraceLine className={className} />
  );
}
