"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import type { Base } from "@/lib/routes";
import { convertLegacy } from "@/lib/search";
import { DEFAULT_FILTERS, RunsList, type ListFilters, type RangeKey } from "./RunsList";

export function parseFilters(sp: URLSearchParams): ListFilters {
  const range = sp.get("range");
  // `?status=FAILED&agent=x` from an older bookmark becomes `q=status:failed agent:x` (plan F11).
  const legacy = convertLegacy(sp);
  return {
    q: legacy.q,
    statuses: legacy.statuses,
    agent: legacy.agent,
    range: (["1h", "24h", "7d"] as const).includes(range as "1h") ? (range as RangeKey) : "all",
  };
}

/** Filters live in the URL so a filtered view can be shared or reloaded. */
export function RunsListPage({ base }: { base: Base }) {
  const path = usePathname();
  const sp = useSearchParams();

  // Local state is the source of truth while the page is open (instant, controlled inputs); the URL mirrors it.
  const [filters, setFilters] = useState<ListFilters>(() => parseFilters(sp));

  // The top-bar search navigates here with `?q=` while this page is open. `mine` is the query string this page last
  // wrote itself (the URL catches up with it a render later, so only a change of the URL is looked at): a URL that
  // changed to something else came from elsewhere and replaces the filters. The string comparison is load-bearing: `mine`
  // is built with `URLSearchParams.toString()` below and `urlText` comes from the same serialiser, so the two encode `,`
  // and spaces identically; encoding either side differently (say `encodeURIComponent`) would make this page treat its
  // own write as a foreign URL and break back/forward adoption.
  const urlText = sp.toString();
  const [seen, setSeen] = useState(urlText);
  const [mine, setMine] = useState(urlText);
  if (seen !== urlText) {
    setSeen(urlText);
    if (urlText !== mine) {
      setMine(urlText);
      setFilters(parseFilters(sp));
    }
  }

  const onFilters = useCallback(
    (f: ListFilters) => {
      setFilters(f);
      const p = new URLSearchParams();
      if (f.q) p.set("q", f.q); // URLSearchParams percent-encodes it (including `+` and `&`)
      if (f.statuses.length) p.set("status", f.statuses.join(","));
      if (f.agent) p.set("agent", f.agent);
      if (f.range !== DEFAULT_FILTERS.range) p.set("range", f.range);
      const qs = p.toString();
      setMine(qs);
      window.history.replaceState(null, "", qs ? `${path}?${qs}` : path);
    },
    [path],
  );

  return <RunsList base={base} filters={filters} onFilters={onFilters} />;
}
