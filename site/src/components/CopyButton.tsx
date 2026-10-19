"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

// false while rendering on the server and during hydration, true afterwards: the button is enabled only once it can work.
const noop = () => () => {};
const useHydrated = () => useSyncExternalStore(noop, () => true, () => false);

/**
 * The only client code a code block needs. Server-rendered as a complete, disabled "Copy" button (the block itself is
 * `user-select: all`, so selecting by hand works without script); enabled on hydration. Writes the text without prompts or notes.
 * Without the Clipboard API (an insecure origin, an old browser) it selects the block so the person can press Ctrl/Cmd+C.
 */
export function CopyButton({ text, targetId }: { text: string; targetId: string }) {
  const ready = useHydrated();
  const [state, setState] = useState<"idle" | "copied" | "select">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const done = (s: "copied" | "select") => {
    setState(s);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1500);
  };
  const selectBlock = () => {
    const el = document.getElementById(targetId);
    if (!el) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    done("select");
  };
  const onClick = async () => {
    try {
      if (!navigator.clipboard) return selectBlock();
      await navigator.clipboard.writeText(text);
      done("copied");
    } catch {
      selectBlock();
    }
  };

  return (
    <>
      <button type="button" className="copy" disabled={!ready} onClick={onClick}>
        {state === "copied" ? "Copied" : "Copy"}
      </button>
      <span className="sr" role="status">
        {state === "copied" ? "Copied to the clipboard" : state === "select" ? "Selected. Press Ctrl or Cmd and C to copy." : ""}
      </span>
    </>
  );
}
