"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import { ALL_STATUSES, type RunStatus } from "@/lib/api/types";
import type { Base } from "@/lib/routes";
import { DEFAULT_FILTERS, RunsList, type ListFilters, type RangeKey } from "./RunsList";

export function parseFilters(sp: URLSearchParams): ListFilters {
  const range = sp.get("range");
  return {
    statuses: (sp.get("status")?.split(",") ?? []).filter((s): s is RunStatus =>
      (ALL_STATUSES as readonly string[]).includes(s),
    ),
    agent: sp.get("agent") ?? "",
    range: (["1h", "24h", "7d"] as const).includes(range as "1h") ? (range as RangeKey) : "all",
  };
}

/** Filters live in the URL so a filtered view can be shared or reloaded. */
export function RunsListPage({ base }: { base: Base }) {
  const path = usePathname();
  const sp = useSearchParams();

  // Local state is the source of truth while the page is open (instant, controlled inputs); the URL mirrors it.
  const [filters, setFilters] = useState<ListFilters>(() => parseFilters(sp));

  const onFilters = useCallback(
    (f: ListFilters) => {
      setFilters(f);
      const p = new URLSearchParams();
      if (f.statuses.length) p.set("status", f.statuses.join(","));
      if (f.agent) p.set("agent", f.agent);
      if (f.range !== DEFAULT_FILTERS.range) p.set("range", f.range);
      const qs = p.toString();
      window.history.replaceState(null, "", qs ? `${path}?${qs}` : path);
    },
    [path],
  );

  return <RunsList base={base} filters={filters} onFilters={onFilters} />;
}
