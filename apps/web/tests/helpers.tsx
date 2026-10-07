import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { fixtureReply } from "@/fixtures/api";

/** Routes the typed client's requests to the in-memory fixture API; `overrides` can force failures per path. */
export function stubApi(overrides: Record<string, () => Response> = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      calls.push(url.pathname + url.search);
      const hit = Object.keys(overrides).find((k) => url.pathname.endsWith(k));
      if (hit) return overrides[hit]!();
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams);
      return Response.json(r.body, { status: r.status });
    }),
  );
  return calls;
}

export function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

export const BASE = { workspace: "demo", project: "all" };
