import { useCallback, useEffect, useRef } from "react";

/**
 * Fire-and-forget timers owned by a component: each one forgets itself when it fires, so a component that stays
 * mounted for a day-long live run holds only the timers that are actually pending, and unmounting clears those.
 */
export function useTimers() {
  const pending = useRef<Set<number>>(new Set());
  const later = useCallback((fn: () => void, ms: number) => {
    const id = window.setTimeout(() => {
      pending.current.delete(id);
      fn();
    }, ms);
    pending.current.add(id);
  }, []);
  useEffect(() => {
    const set = pending.current;
    return () => {
      for (const t of set) window.clearTimeout(t);
      set.clear();
    };
  }, []);
  return { later, pending: () => pending.current.size };
}
