"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import { ALL_STATUSES, type RunStatus } from "@/lib/api/types";
import type { Base } from "@/lib/routes";
import { DEFAULT_FILTERS, RunsList, type ListFilters, type RangeKey } from "./RunsList";

/** Filters live in the URL so a filtered view can be shared or reloaded. */
export function RunsListPage({ base }: { base: Base }) {
  const router = useRouter();
  const path = usePathname();
  const sp = useSearchParams();
  const spString = sp.toString();

  const filters = useMemo<ListFilters>(() => {
    const p = new URLSearchParams(spString);
    const range = p.get("range");
    return {
      statuses: (p.get("status")?.split(",") ?? []).filter((s): s is RunStatus =>
        (ALL_STATUSES as readonly string[]).includes(s),
      ),
      agent: p.get("agent") ?? "",
      range: (["1h", "24h", "7d"] as const).includes(range as "1h") ? (range as RangeKey) : "all",
    };
  }, [spString]);

  const onFilters = useCallback(
    (f: ListFilters) => {
      const p = new URLSearchParams();
      if (f.statuses.length) p.set("status", f.statuses.join(","));
      if (f.agent) p.set("agent", f.agent);
      if (f.range !== DEFAULT_FILTERS.range) p.set("range", f.range);
      const qs = p.toString();
      router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
    },
    [router, path],
  );

  return <RunsList base={base} filters={filters} onFilters={onFilters} />;
}
