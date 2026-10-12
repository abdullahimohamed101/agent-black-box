import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Crumbs } from "@/components/shell/Crumbs";
import { BASE, stubApi } from "./helpers";

const RUN = `run_${"2".padStart(26, "0")}`;
let pathname = `/w/demo/projects/all/runs/${RUN}`;
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

beforeEach(() => {
  pathname = `/w/demo/projects/all/runs/${RUN}`;
});
afterEach(() => vi.unstubAllGlobals());

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("the run crumb and hydration (React #418)", () => {
  it("never starts the run query itself: a reply landing before the page hydrates would change what RunDetail renders first", async () => {
    const calls = stubApi();
    const qc = client();
    render(
      <QueryClientProvider client={qc}>
        <Crumbs base={BASE} />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(calls.filter((c) => c.includes(`/runs/${RUN}`))).toEqual([]);
    expect(qc.getQueryCache().find({ queryKey: ["run", RUN] })).toBeUndefined();
  });

  it("server-renders the short id, then shows the name once the page has put the run in the cache", () => {
    const qc = client();
    const tree = (
      <QueryClientProvider client={qc}>
        <Crumbs base={BASE} />
      </QueryClientProvider>
    );
    expect(renderToString(tree)).toContain(RUN.slice(-8));
    render(tree);
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(RUN.slice(-8));
    act(() => qc.setQueryData(["run", RUN], { name: "Ship <b>it</b>" }));
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Ship <b>it</b>");
  });
});
