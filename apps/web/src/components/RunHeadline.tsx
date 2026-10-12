import type { HeadlineKind, HeadlineModel } from "@/lib/runHeadline";
import "../app/run-header.css";

/**
 * The run page's headline, in one fixed place (ADR-111). Six states, each with a skeleton (same frame, no layout shift),
 * an error (plain text and a retry) and a withheld note for roles that cannot read content. The words come from
 * `headlineModel`; this only lays them out.
 */

export type HeadlineProps =
  | { kind: HeadlineKind; variant: "skeleton"; label?: string }
  | { kind: HeadlineKind; variant: "error"; onRetry: () => void }
  | { kind: HeadlineKind; variant: "ready"; model: HeadlineModel; onGo: () => void };

const ERROR_TEXT: Record<HeadlineKind, string> = {
  failed: "Could not load the details of this failure.",
  recovered: "Could not load the details of this recovery.",
  running: "Could not load the latest activity of this run.",
  waiting: "Could not load what this run is waiting on.",
  success: "Could not load the details of this run.",
  cancelled: "Could not load the details of this cancellation.",
};

/** Keeps "(+6.2 s)" in one piece when the sentence wraps on a phone; the text itself is unchanged. */
function Lead({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\(\+[^)]+\))/).map((part, i) =>
        i % 2 ? (
          <span key={i} className="hl-nowrap">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

function GoControl({ onGo }: { onGo: () => void }) {
  // The visible word is "Go"; the accessible name stays "Show it" (the name every shortcut hint and test uses).
  return (
    <button
      type="button"
      className="hl-go"
      aria-label="Show it"
      title="Jump to the first error (e)"
      onClick={onGo}
    >
      Go <kbd aria-hidden="true">e</kbd>
    </button>
  );
}

export function RunHeadline(props: HeadlineProps) {
  const { kind } = props;
  if (props.variant === "skeleton") {
    return (
      <div className="hl" data-kind={kind} data-variant="skeleton" aria-busy="true">
        <span className="sr-only" role="status">
          {props.label ?? "Loading the run summary"}
        </span>
        <div className="hl-lead hl-skel" aria-hidden="true">
          <span className="hl-bar" />
          <span className="hl-bar hl-bar-short" />
        </div>
        <div className="hl-detail hl-skel" aria-hidden="true">
          <span className="hl-bar hl-bar-mono" />
        </div>
      </div>
    );
  }
  if (props.variant === "error") {
    return (
      <div className="hl" data-kind={kind} data-variant="error">
        <p className="hl-lead hl-error-text" role="alert" data-testid="headline">
          {ERROR_TEXT[kind]}{" "}
          <button type="button" className="hl-go" onClick={props.onRetry}>
            Try again
          </button>
        </p>
        <p className="hl-detail" />
      </div>
    );
  }
  const m = props.model;
  return (
    <div className="hl" data-kind={m.kind} data-variant="ready">
      <p className="hl-lead" data-testid="headline">
        {m.live && <span className="hl-dot" aria-hidden="true" />}
        {m.failure ? (
          <span data-testid="first-error">
            <Lead text={m.lead} /> <GoControl onGo={props.onGo} />
          </span>
        ) : (
          <Lead text={m.lead} />
        )}
      </p>
      <p className="hl-detail" data-testid="headline-detail" data-pending={m.pending || undefined}>
        {m.pending ? <span className="hl-bar hl-bar-mono" aria-hidden="true" /> : m.detail}
        {m.failure?.withheld && <span className="hl-note"> · details withheld for your role</span>}
      </p>
    </div>
  );
}
