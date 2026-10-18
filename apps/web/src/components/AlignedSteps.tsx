"use client";

import { useEffect, useId, useRef, useState } from "react";
import { compare } from "@/copy/run";
import type { RowOut, StepOut } from "@/lib/api/types";
import {
  RELATION_LABEL,
  ROW_PAGE,
  differenceIndexes,
  nextDifference,
  stepMeta,
  stepText,
  visibleCount,
} from "@/lib/comparison";

/** Same-in-both rows kept above the divergence when the earlier ones fold away. */
const CONTEXT_ROWS = 3;

function Cell({ step, side }: { step: StepOut | null; side: "left" | "right" }) {
  if (!step)
    return (
      <div
        className="step-cell"
        data-empty="true"
        data-side={side}
        data-label={compare.sides[side]}
      >
        <span className="muted">{compare.noStep}</span>
      </div>
    );
  const t = stepText(step);
  return (
    <div
      className="step-cell"
      data-side={side}
      data-label={compare.sides[side]}
      data-event-id={step.event_id}
    >
      <span className="step-kind">{t.kind}</span>{" "}
      {t.label !== null && <span className="step-label">{t.label}</span>}
      {t.withheld && <span className="muted"> (details withheld)</span>}
      {stepMeta(step) && <span className="muted step-meta"> {stepMeta(step)}</span>}
    </div>
  );
}

/**
 * The two runs' steps side by side. `rows` come from the API already aligned (its `steps[index]` is the divergence
 * row); this component only lays them out, marks the divergence and the first outcome difference, and moves focus.
 * Free text (labels) is rendered as text. Keyboard: Up/Down/Home/End move between rows, `n` and `p` jump to the next
 * and previous difference, and the toolbar's buttons do the same.
 */
export function AlignedSteps({
  rows,
  divergence,
  outcome,
}: {
  rows: readonly RowOut[];
  divergence: number | null;
  outcome: number | null;
}) {
  const heading = useId();
  const [shown, setShown] = useState(ROW_PAGE);
  // The steps before the divergence are the same in both runs; the page opens a few of them above the cut, not at step 1
  // (plan D12). `from` is the first rendered row; any jump to an earlier row, or the button, lowers it.
  const [from, setFrom] = useState(() =>
    divergence !== null && divergence > CONTEXT_ROWS + 1 ? divergence - CONTEXT_ROWS : 0,
  );
  const [active, setActive] = useState(divergence ?? 0);
  const [want, setWant] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");
  const refs = useRef(new Map<number, HTMLLIElement>());
  const diffs = differenceIndexes(rows);
  const count = visibleCount(shown, rows.length, want);

  // Move focus after the row that was asked for has rendered.
  useEffect(() => {
    if (want === null) return;
    const el = refs.current.get(want);
    if (!el) return;
    el.focus();
    el.scrollIntoView?.({ block: "center" });
    setWant(null);
  }, [want, count]);

  const go = (index: number | null, message?: string) => {
    if (index === null) return;
    setShown((s) => Math.max(s, index + 21));
    setFrom((f) => Math.min(f, index));
    setActive(index);
    setWant(index);
    if (message) setAnnounce(message);
  };
  const describe = (i: number) =>
    `${RELATION_LABEL[rows[i]!.relation]} at step ${i + 1} of ${rows.length}.`;

  const onKeyDown = (e: React.KeyboardEvent) => {
    const t = e.target as HTMLElement;
    if (t.tagName === "BUTTON" || t.tagName === "A") return;
    const last = Math.max(0, count - 1);
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        go(Math.min(active + 1, last));
        break;
      case "ArrowUp":
        e.preventDefault();
        go(Math.max(active - 1, 0));
        break;
      case "Home":
        e.preventDefault();
        go(0);
        break;
      case "End":
        e.preventDefault();
        go(last);
        break;
      case "n": {
        const i = nextDifference(diffs, active, 1);
        go(i, i === null ? "No later difference." : describe(i));
        break;
      }
      case "p": {
        const i = nextDifference(diffs, active, -1);
        go(i, i === null ? "No earlier difference." : describe(i));
        break;
      }
    }
  };

  if (rows.length === 0)
    return (
      <p className="muted" data-testid="no-steps">
        Neither run has steps to compare.
      </p>
    );
  return (
    <div className="aligned" data-testid="aligned-steps">
      <div className="aligned-toolbar" role="group" aria-labelledby={heading}>
        <span id={heading} className="muted">
          {compare.toolbar.summary(rows.length, diffs.length)}
        </span>
        <button
          type="button"
          disabled={divergence === null}
          onClick={() =>
            go(divergence, divergence === null ? undefined : `Divergence: ${describe(divergence)}`)
          }
        >
          {compare.toolbar.jump}
        </button>
        <button
          type="button"
          disabled={nextDifference(diffs, active, -1) === null}
          onClick={() => {
            const i = nextDifference(diffs, active, -1);
            go(i, i === null ? undefined : describe(i));
          }}
        >
          {compare.toolbar.previous}
        </button>
        <button
          type="button"
          disabled={nextDifference(diffs, active, 1) === null}
          onClick={() => {
            const i = nextDifference(diffs, active, 1);
            go(i, i === null ? undefined : describe(i));
          }}
        >
          {compare.toolbar.next}
        </button>
      </div>
      <p className="muted aligned-hint">{compare.hint}</p>
      <p role="status" aria-live="polite" className="aligned-announce" data-testid="aligned-live">
        {announce}
      </p>
      <div className="aligned-head" aria-hidden="true">
        <span>Step</span>
        <span>A, left run</span>
        <span>B, right run</span>
      </div>
      {from > 0 && (
        <button
          type="button"
          className="aligned-earlier"
          data-testid="show-earlier-steps"
          onClick={() => setFrom(0)}
        >
          {compare.earlier(from)}
        </button>
      )}
      <ol className="aligned-list" aria-label="Aligned steps" onKeyDown={onKeyDown}>
        {rows.slice(from, count).map((r, k) => {
          const i = from + k;
          return (
            <li
              key={i}
              ref={(el) => {
                if (el) refs.current.set(i, el);
                else refs.current.delete(i);
              }}
              tabIndex={i === active ? 0 : -1}
              className="aligned-row"
              data-testid="aligned-row"
              data-index={i}
              data-relation={r.relation}
              data-divergence={i === divergence ? "true" : undefined}
              data-after-divergence={divergence !== null && i > divergence ? "true" : undefined}
              data-outcome={i === outcome ? "true" : undefined}
              onFocus={() => setActive(i)}
            >
              <div className="aligned-flags">
                <span className="muted">{i + 1}</span>
                {r.relation !== "equal" && (
                  <span className="tag">{RELATION_LABEL[r.relation]}</span>
                )}
                {i === divergence && (
                  <span className="badge" data-testid="divergence-flag">
                    Divergence
                  </span>
                )}
                {i === outcome && (
                  <span className="badge" data-tone="bad" data-testid="outcome-flag">
                    {compare.outcome}
                  </span>
                )}
              </div>
              <Cell step={r.left} side="left" />
              <Cell step={r.right} side="right" />
            </li>
          );
        })}
      </ol>
      {count < rows.length && (
        <button
          type="button"
          data-testid="show-more-steps"
          onClick={() => setShown((s) => s + ROW_PAGE)}
        >
          Show more steps ({rows.length - count} hidden)
        </button>
      )}
    </div>
  );
}
