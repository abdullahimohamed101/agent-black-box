"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchReadiness, type HealthResult } from "@/lib/api";

type View = { state: "loading" } | HealthResult;

export function ApiHealth() {
  const [view, setView] = useState<View>({ state: "loading" });

  const check = useCallback(async () => {
    setView({ state: "loading" });
    setView(await fetchReadiness());
  }, []);

  useEffect(() => {
    // Initial fetch on mount; state is set after the awaited request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void check();
  }, [check]);

  return (
    <section aria-labelledby="api-health-title" className="panel">
      <h2 id="api-health-title">API status</h2>
      <p role="status" data-state={view.state}>
        {view.state === "loading" && "Checking API…"}
        {view.state === "ok" && `● Ready (API v${view.version}, database connected)`}
        {view.state === "error" && `✕ Not ready: ${view.message}`}
        {view.state === "unreachable" && `✕ API unreachable: ${view.message}`}
      </p>
      {view.state === "error" && view.requestId && (
        <p className="muted">Request ID: {view.requestId}</p>
      )}
      <button type="button" onClick={() => void check()}>
        Re-check
      </button>
    </section>
  );
}
