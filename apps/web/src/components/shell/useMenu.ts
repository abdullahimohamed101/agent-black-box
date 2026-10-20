"use client";

import { useEffect, useState } from "react";

/**
 * A disclosure that is open for one pathname only: navigating closes it with no effect and no stale state. Escape closes
 * and returns focus to the trigger (the first `[aria-expanded]` button in the container); a pointer press outside, or focus
 * leaving the container, closes it. Nothing here depends on hover. The container is held in state through a callback ref,
 * so no ref object is read while rendering.
 */
export function useMenu(pathname: string) {
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [root, attach] = useState<HTMLElement | null>(null);
  const open = openPath === pathname;

  useEffect(() => {
    if (!open || !root) return;
    const onDown = (e: PointerEvent) => {
      if (!root.contains(e.target as Node)) setOpenPath(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setOpenPath(null);
      root.querySelector<HTMLElement>("button[aria-expanded]")?.focus();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, root]);

  return {
    open,
    attach,
    toggle: () => setOpenPath(open ? null : pathname),
    /** Put on the container: closes when focus moves somewhere outside it (Tab out). */
    onBlur: (e: React.FocusEvent) => {
      if (e.relatedTarget && !root?.contains(e.relatedTarget as Node)) setOpenPath(null);
    },
  };
}
