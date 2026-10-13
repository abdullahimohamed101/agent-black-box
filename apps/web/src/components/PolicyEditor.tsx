"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";
import { lineCount, offsetOf, type PolicyIssue } from "@/lib/policy";
import "../app/policy.css";

export type EditorHandle = { goTo: (line: number, column: number) => void };

/**
 * A plain monospace editor: a textarea with a line-number gutter (no editor library). The gutter is decoration; it follows
 * the textarea's scroll, and lines with a problem are marked. `goTo` puts the caret on a line:column and scrolls to it.
 */
export const PolicyEditor = forwardRef<
  EditorHandle,
  {
    value: string;
    onChange?: (text: string) => void;
    readOnly?: boolean;
    issues?: readonly PolicyIssue[];
    label: string;
    describedBy?: string;
  }
>(function PolicyEditor(
  { value, onChange, readOnly = false, issues = [], label, describedBy },
  ref,
) {
  const area = useRef<HTMLTextAreaElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const bad = new Set(issues.map((i) => i.line));
  const n = lineCount(value);

  useImperativeHandle(ref, () => ({
    goTo(line, column) {
      const el = area.current;
      if (!el) return;
      const pos = offsetOf(el.value, line, column);
      el.focus();
      el.setSelectionRange(pos, pos);
      const lh = parseFloat(getComputedStyle(el).lineHeight) || 22;
      el.scrollTop = Math.max(0, (line - 3) * lh);
      if (gutter.current) gutter.current.scrollTop = el.scrollTop;
    },
  }));

  return (
    <div className="pol-editor" data-readonly={readOnly || undefined}>
      <div className="pol-gutter" ref={gutter} aria-hidden="true" data-testid="gutter">
        {Array.from({ length: n }, (_, i) => (
          <span key={i} data-issue={bad.has(i + 1) || undefined}>
            {i + 1}
          </span>
        ))}
      </div>
      <textarea
        ref={area}
        value={value}
        readOnly={readOnly}
        aria-label={label}
        aria-readonly={readOnly || undefined}
        aria-describedby={describedBy}
        aria-invalid={issues.length > 0 || undefined}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        wrap="off"
        onChange={(e) => onChange?.(e.target.value)}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop;
        }}
      />
    </div>
  );
});
