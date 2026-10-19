"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import type { TraceModel } from "../lib/trace";

const GLYPH = { error: "✕ error", retry: "↻ retry" } as const;

/**
 * The hero: a failed run drawn from fixture data, as the dashboard draws it (brief 4.1 and section 6). It renders in full on the
 * server (every row, the first error selected, its inspector), so it reads with JavaScript off; the island only adds selection.
 * The fault line is CSS on the first-error row and draws whether or not this hydrates. A listbox, as the brief specifies:
 * one tab stop, j/k and the arrows move, Home/End jump, e goes to the first error, Enter and Space select, the mouse hovers.
 * The key handling is scoped to the list (a page-wide `j` would steal keys from a screen reader).
 */
export function FaultTrace({ model }: { model: TraceModel }) {
  const [sel, setSel] = useState(model.firstErrorIndex);
  const rowRefs = useRef<(HTMLLIElement | null)[]>([]);
  const last = model.rows.length - 1;
  const row = model.rows[sel]!;

  const go = (i: number) => {
    const n = Math.max(0, Math.min(last, i));
    setSel(n);
    rowRefs.current[n]?.focus();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key;
    const handled = ["j", "ArrowDown", "k", "ArrowUp", "e", "Home", "End", "Enter", " "].includes(key);
    if (!handled) return;
    e.preventDefault();
    if (key === "j" || key === "ArrowDown") go(sel + 1);
    else if (key === "k" || key === "ArrowUp") go(sel - 1);
    else if (key === "e") go(model.firstErrorIndex);
    else if (key === "Home") go(0);
    else if (key === "End") go(last);
    else {
      // Enter and Space "open": the selection already shows in the inspector; on one column that sits below, so bring it into view.
      document.getElementById("trace-inspector")?.scrollIntoView({ block: "nearest" });
    }
  };

  return (
    <figure className="trace" aria-label="A failed run, as the product shows it">
      <div className="trace-main">
        <div className="trace-head">
          <p className="crumb">
            Runs / <b>{model.title}</b> <span className="fail head-fail">✕ Failed</span> <span className="quiet">· {model.date}</span>
          </p>
          <p className="headline">{model.headline}</p>
          <p className="figs">
            Duration <code>{model.figures.duration}</code> Cost <code>{model.figures.cost}</code> Tokens <code>{model.figures.tokens}</code> Retries{" "}
            <code>{model.figures.retries}</code> Errors <code>{model.figures.errors}</code>
          </p>
        </div>
        <ul className="rows" role="listbox" aria-label="Events, first error selected" onKeyDown={onKeyDown}>
          {model.rows.map((r, i) => (
            <li
              key={r.seq}
              ref={(el) => {
                rowRefs.current[i] = el;
              }}
              id={`trace-row-${r.seq}`}
              role="option"
              aria-selected={i === sel}
              tabIndex={i === sel ? 0 : -1}
              data-step={r.seq}
              className={`row${i === model.firstErrorIndex ? " first-error" : ""}`}
              onFocus={() => setSel(i)}
              onClick={() => setSel(i)}
              onPointerEnter={(e) => {
                if (e.pointerType === "mouse") setSel(i);
              }}
            >
              <span className="t">{r.t}</span>
              <span className="what">
                {i === model.firstErrorIndex ? <span className="sr">First error, step {model.firstErrorStep}. </span> : null}
                {r.type}
                {r.detail ? <span className="detail"> {r.detail}</span> : null}
              </span>
              <span className="dur">{r.dur}</span>
              <span className={`st${r.status === "error" ? " fail" : r.status === "retry" ? " caution" : ""}`}>{r.status ? GLYPH[r.status] : ""}</span>
            </li>
          ))}
        </ul>
        <div className="trace-foot">
          <span>
            {model.rows.length} of {model.total} events shown
          </span>
          <span className="hint">
            <kbd>j</kbd> and <kbd>k</kbd> move, <kbd>Enter</kbd> opens, <kbd>e</kbd> jumps to the first error
          </span>
        </div>
      </div>
      <aside className="insp" id="trace-inspector" aria-live="polite" aria-label="Selected event">
        <div className="insp-body" key={row.seq}>
          <p className="insp-title">{row.inspector.type}</p>
          <p className="meta">{row.inspector.meta}</p>
          <p className="insp-h">{row.inspector.heading}</p>
          <p className="insp-text">{row.inspector.text}</p>
          {row.inspector.facts.length ? (
            <dl>
              {row.inspector.facts.map(([k, v]) => (
                <div key={k} className="fact">
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {row.inspector.payload ? (
            <>
              <p className="insp-h">Payload</p>
              <pre tabIndex={0}>{row.inspector.payload}</pre>
            </>
          ) : null}
        </div>
      </aside>
    </figure>
  );
}
