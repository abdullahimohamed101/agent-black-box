import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalsPage } from "@/components/ApprovalsPage";
import { ApprovalsCount } from "@/components/ApprovalsCount";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { resetPolicyFixtures } from "@/fixtures/policies";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { apr, ctx, errorBody, mount, stubPolicyApi, OWNER } from "./policyHelpers";

// The URL is the single source of truth: a tiny store stands in for Next's search params, fed by history.pushState.
const url = vi.hoisted(() => ({ search: "", subs: new Set<() => void>() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/approvals",
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
// The page's clock, driven by the test: `now` is the real time plus an offset the test moves.
const clock = vi.hoisted(() => ({ offset: 0, subs: new Set<() => void>() }));
vi.mock("@/lib/useNow", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useNow: () =>
      useSyncExternalStore(
        (n) => {
          clock.subs.add(n);
          return () => clock.subs.delete(n);
        },
        () => clock.offset,
        () => 0,
      ) + Date.now(),
  };
});
const skip = (ms: number) => {
  clock.offset += ms;
  clock.subs.forEach((n) => n());
};
const realPush = window.history.pushState.bind(window.history);
beforeEach(() => {
  clock.offset = 0;
  resetPolicyFixtures();
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
  vi.useRealTimers();
});
const open = (id: string, state = "") => {
  url.search = `${state ? `state=${state}&` : ""}approval=${id}`;
};

describe("the Approvals page list", () => {
  // Step 5c (03 section 4): the list has five columns (Request, Risk, State, Requested, Time left). The run link, the project and
  // the policy are in the request panel, where the next test and the detail tests below assert them.
  it("lists pending requests with capability, scrubbed resource, risk, state and time left", async () => {
    stubPolicyApi();
    mount(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    expect(rows).toHaveLength(6);
    const first = within(rows[0]!);
    expect(first.getByText("github.push")).toBeInTheDocument();
    expect(first.getByText("origin main")).toBeInTheDocument();
    expect(first.getByText("Risk R3")).toBeInTheDocument();
    expect(first.getByText("Waiting")).toBeInTheDocument();
    expect(first.getByTestId("time-left").textContent).toMatch(
      /^\d+ h( \d+ min)? left$|^\d+ min left$/,
    );
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Request",
      "Risk",
      "State",
      "Requested",
      "Time left",
    ]);
    expect(first.queryByText("Demo project")).toBeNull();
    expect(first.queryByText("approve-push-main")).toBeNull();
    expect(screen.getByText(/6 requests loaded/)).toBeInTheDocument();
    // The count of waiting requests is said once (the "N waiting" line of the Review frame), not again in the chip.
    expect(screen.getByRole("tab", { name: "Pending" })).toBeInTheDocument();
  });

  // The state list is the shared Tabs component at chip density: role tab, aria-selected, the state is still the URL's ?state=.
  it("the state tabs are URL state: Decided and All read the API's state, and Load more pages", async () => {
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    await screen.findAllByTestId("approval-row");
    await user.click(screen.getByRole("tab", { name: /^Decided/ }));
    expect(url.search).toBe("state=decided");
    await waitFor(() =>
      expect(
        calls.some((c) => c.path === "v1/approvals" && c.search.includes("state=decided")),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getAllByTestId("approval-row")).toHaveLength(25));
    expect(screen.getByRole("tab", { name: /^Decided/ })).toHaveAttribute("aria-selected", "true");
    const states = screen.getAllByTestId("approval-row").map((r) => r.getAttribute("data-state"));
    expect(states).not.toContain("REQUESTED");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByTestId("approval-row").length).toBeGreaterThan(25));
    await user.click(screen.getByRole("tab", { name: /^All/ }));
    expect(url.search).toBe("state=all");
    await user.click(screen.getByRole("tab", { name: /^Pending/ }));
    expect(url.search).toBe("");
  });

  it("starts on the tab named in the URL", async () => {
    url.search = "state=decided";
    stubPolicyApi();
    mount(<ApprovalsPage />);
    await screen.findAllByTestId("approval-row");
    expect(screen.getByRole("tab", { name: /^Decided/ })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the empty state without an illustration, and a loading status while loading", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/approvals" ? Response.json({ items: [], next_cursor: null }) : undefined,
    );
    mount(<ApprovalsPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading approvals");
    expect(await screen.findByText("Nothing is waiting for you")).toBeInTheDocument();
    // ADR-166: drawings stay on the first-run guide and not-found only.
    expect(screen.getByTestId("approvals-empty").querySelector("svg")).toBeNull();
  });

  it("a failed list offers a retry", async () => {
    let fail = true;
    stubPolicyApi((c) =>
      fail && c.path === "v1/approvals" ? errorBody("API_UNREACHABLE", 503) : undefined,
    );
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load this data");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByTestId("approval-row")).length).toBeGreaterThan(0);
  });
});

describe("role gating", () => {
  it("a role without approval.read gets 'Not available for your role' and sends no request", async () => {
    const calls = stubPolicyApi();
    mount(<ApprovalsPage />, ["run.read"]);
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
    await act(async () => {});
    expect(calls).toHaveLength(0);
  });

  it("the API's 403 on the list also shows 'Not available'", async () => {
    stubPolicyApi(() => errorBody("PERMISSION_DENIED", 403));
    mount(<ApprovalsPage />);
    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
  });

  it("a reader who cannot decide sees the request and no Approve or Deny", async () => {
    open(apr(3));
    stubPolicyApi();
    mount(<ApprovalsPage />, ["run.read", "approval.read"]);
    expect(
      await screen.findByText("Your role can read this request but not decide it."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deny" })).toBeNull();
  });
});

describe("the detail panel", () => {
  it("shows the intent exactly as the API returns it, as text", async () => {
    open(apr(3));
    stubPolicyApi();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    const text = panel.textContent ?? "";
    expect(text).toContain("database.execute");
    expect(text).toContain("orders");
    expect(text).toContain("Risk R3");
    expect(text).toContain("Admin or above");
    expect(text).toContain("approve-db-write");
    expect(text).toContain("v3");
    expect(text).toMatch(/if it expires, the action is denied/);
    expect(panel.querySelector("pre")?.textContent).toBe(
      JSON.stringify({ statement: "ALTER TABLE orders ADD COLUMN note text" }, null, 2),
    );
    expect(within(panel).getByRole("link", { name: /^run …/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/runs\/run_/),
    );
    expect(screen.getByRole("heading", { level: 2, name: "database.execute" })).toHaveFocus();
  });

  it("a row opens it (URL state), Escape and Close shut it and focus returns to the row", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const rows = await screen.findAllByTestId("approval-row");
    await user.click(within(rows[1]!).getByRole("link", { name: "shell.execute" }));
    expect(url.search).toMatch(/^approval=apr_/);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("approval-detail")).toBeNull());
    expect(url.search).toBe("");
    await waitFor(() => expect(document.activeElement?.id).toMatch(/^apr-open-apr_/));
  });

  it("an unknown id says so in the API's fixed words", async () => {
    open(apr(999));
    stubPolicyApi();
    mount(<ApprovalsPage />);
    expect(await screen.findByText("This approval was not found.")).toBeInTheDocument();
  });

  it("approve: comment, a confirm step, then the API's answer replaces the panel and the row", async () => {
    open(apr(2));
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.type(within(panel).getByLabelText("Comment (optional)"), "Looks right.");
    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    // nothing is sent until the confirm step
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(within(panel).getByRole("button", { name: "Confirm approve" })).toHaveFocus();
    await user.click(within(panel).getByRole("button", { name: "Confirm approve" }));
    expect(await within(panel).findByTestId("decision-saved")).toHaveTextContent(
      "You approved this request.",
    );
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`v1/approvals/${apr(2)}/decision`);
    expect(post.body).toEqual({ decision: "approve", comment: "Looks right." });
    expect(within(panel).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(panel).getAllByText("Approved").length).toBeGreaterThan(0);
    // the pending list is read again: the decided request has left it
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("approval-row")
          .map((r) => r.textContent)
          .join(),
      ).not.toContain(`shell.execute…kubectl`),
    );
  });

  it("the confirm step can be cancelled with the button or Escape, and sends nothing", async () => {
    open(apr(3));
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.click(within(panel).getByRole("button", { name: "Deny" }));
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(within(panel).getByRole("button", { name: "Deny" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Deny" }));
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("approval-detail")).toBeInTheDocument(); // Escape undid the confirm only
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("deny with a comment, keyboard only", async () => {
    open(apr(3));
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    const comment = await within(panel).findByLabelText("Comment (optional)");
    comment.focus();
    await user.keyboard("Not on a shared host.");
    await user.tab();
    expect(within(panel).getByRole("button", { name: "Approve" })).toHaveFocus();
    await user.tab();
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}"); // the confirm button has the focus
    expect(await within(panel).findByTestId("decision-saved")).toHaveTextContent(
      "You denied this request.",
    );
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      decision: "deny",
      comment: "Not on a shared host.",
    });
  });

  it("a comment over 1000 characters cannot be sent", async () => {
    open(apr(6));
    const calls = stubPolicyApi();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    const box = await within(panel).findByLabelText("Comment (optional)");
    fireEvent.change(box, { target: { value: "x".repeat(1001) } });
    expect(within(panel).getByText(/1001 \/ 1000 \(too long\)/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it.each([
    [4, "SELF_APPROVAL_FORBIDDEN", /You cannot decide a request made with your own API key\./],
    [5, "APPROVER_ROLE_REQUIRED", /^Your role cannot decide this request\.$/],
    [6, "APPROVAL_NOT_REQUESTED", /^Someone else decided first: it is now denied\.$/],
  ])("approval %i answers %s with a fixed sentence", async (n, _code, sentence) => {
    open(apr(n));
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    await user.click(within(panel).getByRole("button", { name: "Confirm approve" }));
    const alert = await within(panel).findByTestId("decision-error");
    expect(alert.textContent).toMatch(sentence);
    expect(alert.textContent).not.toContain("API sentence");
    expect(within(panel).queryByTestId("decision-saved")).toBeNull();
  });

  it("after losing a race the panel shows who won: the request is read again, not guessed", async () => {
    open(apr(6));
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.click(within(panel).getByRole("button", { name: "Deny" }));
    await user.click(within(panel).getByRole("button", { name: "Confirm deny" }));
    await within(panel).findByTestId("decision-error");
    await waitFor(() =>
      expect(within(panel).queryByRole("button", { name: "Approve" })).toBeNull(),
    );
    expect(within(panel).getByTestId("decision-error")).toHaveTextContent(
      "Someone else decided first",
    );
  });

  it("a 404 and an unexpected error on decide are plain sentences; no body is echoed", async () => {
    open(apr(3));
    stubPolicyApi((c) => (c.method === "POST" ? errorBody("APPROVAL_NOT_FOUND", 404) : undefined));
    const user = userEvent.setup();
    mount(<ApprovalsPage />);
    const panel = await screen.findByTestId("approval-detail");
    await within(panel).findByText("Approver needed");
    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    await user.click(within(panel).getByRole("button", { name: "Confirm approve" }));
    expect(await within(panel).findByTestId("decision-error")).toHaveTextContent(
      "This approval was not found.",
    );
  });
});

describe("the countdown", () => {
  const item = (secondsLeft: number) => ({
    agent_id: "a",
    approver_role: "ADMIN",
    capability: "github.push",
    consumed: false,
    decided_at: null,
    decided_by: null,
    expires_at: "2026-10-13T12:00:00Z",
    id: apr(1),
    policy_id: "r",
    policy_version: 1,
    project_id: "prj_00000000000000000000000001",
    requested_at: "2026-10-13T11:00:00Z",
    resource: "origin main",
    risk_class: "R3",
    run_id: "run_00000000000000000000000001",
    seconds_left: secondsLeft,
    state: "REQUESTED",
  });

  it("ticks down with the server's figure, never goes negative, and ends at Expired", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/approvals"
        ? Response.json({ items: [item(125)], next_cursor: null })
        : undefined,
    );
    mount(<ApprovalsPage />);
    const cell = await screen.findByTestId("time-left");
    expect(cell).toHaveTextContent("2 min left");
    act(() => skip(66_000));
    expect(cell.textContent).toMatch(/^5[6-9] s left$/);
    act(() => skip(80_000));
    expect(cell).toHaveTextContent("Expired");
    for (const ms of [5_000, 60_000, 3_600_000]) {
      act(() => skip(ms));
      expect(cell.textContent).toBe("Expired");
    }
  });

  it("a local clock that jumps back does not add time", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/approvals"
        ? Response.json({ items: [item(605)], next_cursor: null })
        : undefined,
    );
    mount(<ApprovalsPage />);
    const cell = await screen.findByTestId("time-left");
    expect(cell).toHaveTextContent("10 min left");
    act(() => skip(-3_600_000));
    expect(cell.textContent).toBe("10 min left");
  });
});

describe("polling", () => {
  it("re-reads the pending list about every 10 s and stops when the page unmounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = stubPolicyApi();
    const { unmount } = mount(<ApprovalsPage />);
    await screen.findAllByTestId("approval-row");
    const lists = () =>
      calls.filter((c) => c.path === "v1/approvals" && c.search.includes("state=pending")).length;
    const before = lists();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_500);
    });
    expect(lists()).toBe(before + 1);
    unmount();
    const after = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(calls.length).toBe(after);
  });

  it("does not poll the Decided tab", async () => {
    url.search = "state=decided";
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = stubPolicyApi();
    mount(<ApprovalsPage />);
    await screen.findAllByTestId("approval-row");
    const before = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(35_000);
    });
    expect(calls.length).toBe(before);
  });

  it("the rail count reads pending-count about every 30 s, shows a number, and stops on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = stubPolicyApi();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = (await import("@testing-library/react")).render(
      <QueryClientProvider client={client}>
        <ApprovalsCount />
      </QueryClientProvider>,
    );
    const n = () => calls.filter((c) => c.path === "v1/approvals/pending-count").length;
    await waitFor(() => expect(screen.getByTestId("approvals-count")).toBeInTheDocument());
    const first = n();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    expect(n()).toBe(first + 1);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90_000);
    });
    expect(n()).toBe(first + 1);
    cleanup();
  });

  it("the rail count renders nothing when the count is zero or fails", async () => {
    stubPolicyApi((c) =>
      c.path.endsWith("pending-count") ? Response.json({ count: 0 }) : undefined,
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = (await import("@testing-library/react")).render(
      <QueryClientProvider client={client}>
        <ApprovalsCount />
      </QueryClientProvider>,
    );
    await act(async () => {});
    expect(container).toBeEmptyDOMElement();
  });
});

describe("hydration safety", () => {
  it("the server render holds no time-dependent text (rows exist only after the data does)", () => {
    const client = new QueryClient();
    const html = renderToString(
      <QueryClientProvider client={client}>
        <WorkspaceProvider value={ctx(OWNER)}>
          <ApprovalsPage />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );
    expect(html).toContain("Pending"); // the heading is Review's now; the state list is this page's
    expect(html).not.toMatch(/ago|min left|h left|Expired/);
  });
});

describe("no content beyond the known fields", () => {
  it("renders only what it names: an extra field on a row or a detail never reaches the page", async () => {
    stubPolicyApi((c) => {
      if (c.path === "v1/approvals")
        return Response.json({
          items: [
            {
              agent_id: "a",
              approver_role: "ADMIN",
              capability: "github.push",
              consumed: false,
              decided_at: null,
              decided_by: null,
              expires_at: "2099-10-13T12:00:00Z",
              id: apr(1),
              policy_id: "r",
              policy_version: 1,
              project_id: "prj_00000000000000000000000001",
              requested_at: "2026-10-13T11:00:00Z",
              resource: "origin main",
              risk_class: "R3",
              run_id: "run_00000000000000000000000001",
              seconds_left: 100,
              state: "REQUESTED",
              intent_hash: "HASH-SENTINEL",
              raw_command: "COMMAND-SENTINEL",
            },
          ],
          next_cursor: null,
        });
      return undefined;
    });
    mount(<ApprovalsPage />);
    await screen.findByTestId("approval-row");
    const html = document.body.innerHTML;
    expect(html).not.toContain("HASH-SENTINEL");
    expect(html).not.toContain("COMMAND-SENTINEL");
  });
});
