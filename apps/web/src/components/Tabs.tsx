"use client";

import { useId, useRef } from "react";
import "../app/tabs.css";

export type TabItem<T extends string> = {
  id: T;
  label: string;
  /** Shown beside the label. Decoration only (aria-hidden), so the tab keeps its plain name. */
  count?: string | undefined;
};

/**
 * The product's one tab component (ADR-166: one tab component). A `tablist` of buttons with roving tabindex: Left and
 * Right arrows, Home and End move AND select (automatic activation), Tab leaves the list. The panel that a tab controls
 * belongs to the caller: give it `role="tabpanel"`, `id={panelId}` and `aria-labelledby={tabId(value)}`.
 */
export function Tabs<T extends string>({
  label,
  items,
  value,
  onChange,
  idBase,
  className,
}: {
  label: string;
  items: readonly TabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  /** Stable id prefix shared with the panel; defaults to a generated one. */
  idBase?: string;
  className?: string;
}) {
  const generated = useId();
  const base = idBase ?? generated;
  const refs = useRef(new Map<T, HTMLButtonElement>());
  // A list whose current value is not among the items (a role that may not read the open route) is still reachable by keyboard.
  const reachable = items.some((i) => i.id === value) ? value : items[0]?.id;
  const move = (to: number) => {
    const next = items[(to + items.length) % items.length];
    if (!next) return;
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const at = items.findIndex((i) => i.id === value);
    switch (e.key) {
      case "ArrowRight":
        move(at + 1);
        break;
      case "ArrowLeft":
        move(at - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(items.length - 1);
        break;
      default:
        return;
    }
    e.preventDefault();
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      aria-orientation="horizontal"
      className={className ? `tabs ${className}` : "tabs"}
      onKeyDown={onKeyDown}
    >
      {items.map((t) => {
        const selected = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              if (el) refs.current.set(t.id, el);
              else refs.current.delete(t.id);
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`${base}-panel`}
            tabIndex={t.id === reachable ? 0 : -1}
            className="tab"
            onClick={() => onChange(t.id)}
          >
            <span className="tab-label">{t.label}</span>
            {t.count && (
              <span className="tab-count" aria-hidden="true">
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** The ids a panel needs to be controlled by `Tabs` with the same `idBase`. */
export const tabIds = (base: string, value: string) => ({
  id: `${base}-panel`,
  "aria-labelledby": `${base}-tab-${value}`,
});
