"use client";

import { useEffect, useState } from "react";

/**
 * A clock that ticks while the tab is visible and stops while it is hidden (it catches up when the tab returns). The first
 * value is read on the client only after data has arrived, never during a server render, so it cannot cause a hydration
 * mismatch: every caller renders its time-dependent text from fetched data.
 */
export function useNow(intervalMs = 1000, enabled = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    let id: number | undefined;
    const stop = () => {
      if (id !== undefined) window.clearInterval(id);
      id = undefined;
    };
    const start = () => {
      stop();
      id = window.setInterval(() => setNow(Date.now()), intervalMs);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        setNow(Date.now());
        start();
      } else stop();
    };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled]);
  return now;
}
