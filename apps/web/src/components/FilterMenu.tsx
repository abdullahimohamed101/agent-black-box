"use client";

import { useEffect, useId, useRef, useState } from "react";
import { CLASS_LABELS, CLASS_ORDER, type EventClass } from "@/lib/timeline";
import "../app/run-timeline.css";

/**
 * The event-class filter as one button and a popover (Phase 22 step 4), replacing the always-open chip row. The button
 * carries the number of classes currently hidden. A disclosure, not a menu: Tab moves into the checkboxes, Escape or a
 * click or Tab outside closes it and Escape hands focus back to the button.
 */
export function FilterMenu({
  classes,
  onToggle,
  onReset,
}: {
  /** null = every class is shown. */
  classes: ReadonlySet<EventClass> | null;
  onToggle: (c: EventClass) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const hidden = classes ? CLASS_ORDER.filter((c) => !classes.has(c)).length : 0;

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  return (
    <div
      ref={wrap}
      className="filter-menu"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
      onBlur={(e) => {
        if (open && !wrap.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        className="filter-button"
        aria-expanded={open}
        aria-controls={panelId}
        title={hidden ? `${hidden} event class${hidden === 1 ? "" : "es"} hidden` : undefined}
        data-active={hidden > 0 || undefined}
        onClick={() => setOpen((o) => !o)}
      >
        Filter
        {hidden > 0 && (
          <>
            {" "}
            <span className="filter-count">{hidden}</span>
          </>
        )}
        <span className="filter-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      <div
        id={panelId}
        className="filter-panel"
        role="group"
        aria-label="Event classes"
        hidden={!open}
      >
        {CLASS_ORDER.map((c) => (
          <label key={c} className="filter-item">
            <input
              type="checkbox"
              checked={classes ? classes.has(c) : true}
              onChange={() => onToggle(c)}
            />
            {CLASS_LABELS[c]}
          </label>
        ))}
        <button type="button" className="filter-reset" disabled={hidden === 0} onClick={onReset}>
          Show all classes
        </button>
      </div>
    </div>
  );
}
