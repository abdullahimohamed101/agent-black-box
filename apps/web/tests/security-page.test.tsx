import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SecurityPage } from "@/components/SecurityPage";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { SECURITY_SENTINELS } from "@/fixtures/securityRuns";
import { fixtureReply } from "@/fixtures/api";
import type { WorkspaceContext } from "@/lib/workspace";

// The URL is the single source of truth: a tiny store stands in for Next's search params, fed by history.pushState.
const url = vi.hoisted(() => ({ search: "", subs: new Set<() => void>() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/security",
  useSearchParams: () => {
    const s = useSyncExternalStore(
      (n) => {
        url.subs.add(n);
        return () => url.subs.delete(n);
      },
      () => url.search,
      () => "",
    );
    return new URLSearchParams(s);
  },
}));
const realPush = window.history.pushState.bind(window.history);
beforeEach(() => {
  url.search = "";
  window.history.pushState = (_d, _t, u) => {
    realPush(_d, _t, u);
    url.search = new URL(String(u), "http://x").search.slice(1);
    url.subs.forEach((n) => n());
  };
});
afterEach(() => {
  window.history.pushState = realPush;
  vi.unstubAllGlobals();
});

const ctx = (permissions = ["run.read"]): WorkspaceContext => ({
  mode: "session",
  user: { id: "u", email: "me@x.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "demo", name: "Demo" },
  role: "OWNER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [{ id: "prj_00000000000000000000000001", slug: "demo", name: "Demo project" }],
});
function mount(permissions?: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SecurityPage now={Date.parse("2026-10-13T12:00:00Z")} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}
function stub(over?: (u: URL) => Response | undefined) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const u = new URL(typeof input === "string" ? input : input.url);
      calls.push(u.pathname + u.search);
      const o = over?.(u);
      if (o) return o;
      const r = fixtureReply(u.pathname.replace(/^\/api\/abb\//, "").split("/"), u.searchParams);
      return Response.json(r.body, { status: r.status });
    }),
  );
  return calls;
}

describe("the workspace Security page", () => {
  it("lists findings newest run first with glyph and word, the fixed title and the run as a link", async () => {
    stub();
    mount();
    const rows = await screen.findAllByTestId("security-row");
    expect(rows).toHaveLength(10); // the first page
    expect(within(rows[0]!).getByText(/Critical|High|Medium/)).toBeInTheDocument();
    const link = within(rows[0]!).getByRole("link");
    expect(link.getAttribute("href")).toMatch(
      /^\/w\/demo\/projects\/demo\/runs\/run_\w+\?finding=fnd_\w+$/,
    );
    expect(screen.getByRole("columnheader", { name: "Severity" })).toBeInTheDocument();
    const table = screen.getByRole("table");
    for (const s of SECURITY_SENTINELS) expect(table.textContent).not.toContain(s);
  });

  it("pages with 'Load more' until the end of the list", async () => {
    stub();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("security-row");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByTestId("security-row")).toHaveLength(12));
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(screen.getByText(/end of list/)).toBeInTheDocument();
  });

  it("a facet click edits the URL, narrows the list, and a second click undoes it", async () => {
    const calls = stub();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("security-row");
    const group = screen.getByRole("heading", { name: "Severity" }).parentElement!;
    await user.click(within(group).getByRole("button", { name: /^Critical/ }));
    expect(url.search).toBe("severity=critical");
    await waitFor(() => expect(screen.getAllByTestId("security-row")).toHaveLength(3));
    expect(calls.some((c) => c.includes("severity=critical"))).toBe(true);
    expect(within(group).getByRole("button", { name: /^Critical/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(within(group).getByRole("button", { name: /^Critical/ }));
    expect(url.search).toBe("");
  });

  it("starts from the URL, so a shared link shows the same view; project and range are in it", async () => {
    const calls = stub();
    url.search = "detector=sec.risky_git&project=demo&range=30d";
    mount();
    await screen.findAllByTestId("security-row");
    const last = calls.filter((c) => c.includes("/security/findings")).at(-1)!;
    expect(last).toContain("detector=sec.risky_git");
    expect(last).toContain("project_id=prj_00000000000000000000000001");
    expect(last).toContain("since=2026-09-13T12%3A00%3A00.000Z");
  });

  it("ignores a malformed range or time from the URL instead of forwarding it", async () => {
    const calls = stub();
    url.search = "since=yesterday&until=2026-10-13T10:00:00&severity=nope";
    mount();
    await screen.findAllByTestId("security-row");
    const last = calls.filter((c) => c.includes("/security/findings")).at(-1)!;
    expect(last).not.toMatch(/since=|until=|severity=/);
  });

  it("filters matching nothing say so and offer to clear them", async () => {
    stub();
    const user = userEvent.setup();
    url.search = "since=2030-01-01T00:00:00Z";
    mount();
    expect(await screen.findByText("No findings match")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Clear filters" })[0]!);
    expect(url.search).toBe("");
    expect((await screen.findAllByTestId("security-row")).length).toBeGreaterThan(0);
  });

  it("no findings at all: 'Nothing suspicious found' and the detectors that are on", async () => {
    stub((u) =>
      u.pathname.endsWith("/security/findings")
        ? Response.json({
            items: [],
            next_cursor: null,
            facets: { severity: [], detector: [], project: [] },
          })
        : undefined,
    );
    mount();
    const clean = await screen.findByTestId("security-clean");
    expect(clean).toHaveTextContent("Nothing suspicious found");
    expect(await within(clean).findByText("Risky git operation")).toBeInTheDocument();
  });

  it("shows a loading skeleton and then an error with a retry", async () => {
    let fail = true;
    stub((u) =>
      fail && u.pathname.endsWith("/security/findings")
        ? Response.json(
            {
              error: {
                code: "X",
                message: "Boom",
                request_id: "req_1",
                retryable: true,
                details: {},
              },
            },
            { status: 500 },
          )
        : undefined,
    );
    const user = userEvent.setup();
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByTestId("security-row")).length).toBeGreaterThan(0);
  });

  it("the skeleton is announced while the first page loads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    mount();
    expect(await screen.findByText("Loading security findings…")).toBeInTheDocument();
  });

  it("a role without run access (BILLING) gets the 'not available' state and no request", async () => {
    const calls = stub();
    mount(["billing.read"]);
    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
    expect(calls.filter((c) => c.includes("/security/"))).toEqual([]);
  });

  it("a 403 from the API is the same state", async () => {
    stub((u) =>
      u.pathname.endsWith("/security/findings")
        ? Response.json(
            {
              error: {
                code: "PERMISSION_DENIED",
                message: "no",
                request_id: "r",
                retryable: false,
                details: {},
              },
            },
            { status: 403 },
          )
        : undefined,
    );
    mount();
    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
  });
});
