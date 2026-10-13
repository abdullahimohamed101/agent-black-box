"use client";

import { useEffect } from "react";

export const LEAVE_TEXT = "You have unsaved changes to this policy. Leave without publishing?";

/**
 * Warns before unsaved work is lost. A reload, a closed tab or an external link asks through the browser (`beforeunload`);
 * a click on a link to another page of this app asks with a confirm dialog (Next.js has no navigation-blocking hook).
 * The browser's own Back button is covered by `beforeunload` only when it leaves the app.
 */
export function useUnsavedGuard(dirty: boolean, message: string = LEAVE_TEXT) {
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
        return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search)
        return; // an in-page anchor (the skip link) leaves nothing behind
      if (!window.confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty, message]);
}
