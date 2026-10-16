import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AlertsPage } from "@/components/AlertsPage";
import { resetAlertFixtures } from "@/fixtures/alerts";
import {
  buildRuleBody,
  destinationHealth,
  errorCodeWords,
  filterWords,
  newDraft,
  notificationStatus,
  problemFrom,
} from "@/lib/alerts";
import { ApiRequestError } from "@/lib/api/client";
import { errorBody, mount } from "./policyHelpers";
import { MANAGE, READER, stubAlertApi } from "./alertsHelpers";

const url = vi.hoisted(() => ({ search: "", subs: new Set<() => void>() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/settings/alerts",
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
  resetAlertFixtures();
  url.search = "";
  window.history.pushState = (_d, _t, u) => {
    realPush(_d, _t, u);
    url.search = new URL(String(u), "http://x").search.slice(1);
    url.subs.forEach((n) => n());
  };
});
afterEach(() => {
  cleanup();
  window.history.pushState = realPush;
  vi.unstubAllGlobals();
});
const tab = (t: string) => {
  url.search = t === "rules" ? "" : `tab=${t}`;
};

describe("the Alerts page: tabs and the rules table", () => {
  // Step 5c: Rules, Destinations and History are the shared Tabs component (role tab, aria-selected) inside the Alerts tab of
  // Settings; the tab is still the URL's ?tab=, and arrow keys move and select as on every other tab list.
  it("tabs are the shared tab list, and the tab is the URL", async () => {
    stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    expect(screen.getByRole("tablist", { name: "Alert sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "History" }));
    expect(url.search).toBe("tab=history");
    expect(screen.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findAllByTestId("history-row")).not.toHaveLength(0);
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "alerts-tab-history");
  });

  it("arrow keys move between the tabs and the URL follows", async () => {
    stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    screen.getByRole("tab", { name: "Rules" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(url.search).toBe("tab=destinations");
    expect(screen.getByRole("tab", { name: "Destinations" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(url.search).toBe("tab=history");
  });

  it("lists rules with the trigger and filters in words, destinations and last fired", async () => {
    stubAlertApi(true);
    mount(<AlertsPage />, MANAGE);
    const rows = await screen.findAllByTestId("rule-row");
    expect(rows).toHaveLength(2);
    const first = within(rows[0]!);
    expect(first.getByText("Failed runs")).toBeInTheDocument();
    expect(first.getByText("A run fails")).toBeInTheDocument();
    expect(first.getByText("in Demo project")).toBeInTheDocument();
    expect(first.getByText("ending failed, timed out")).toBeInTheDocument();
    expect(first.getByText(/Ops webhook/)).toBeInTheDocument();
    expect(first.getByText(/Team Slack/)).toBeInTheDocument();
    expect(first.getByText(/failing/)).toBeInTheDocument();
    expect(first.getByText(/every 10 minutes/)).toBeInTheDocument();
    expect(first.getByText("2026-10-12 14:30 UTC")).toBeInTheDocument();
    const second = within(rows[1]!);
    expect(second.getByText("at High severity or worse")).toBeInTheDocument();
    expect(second.getByText("by agent release-agent")).toBeInTheDocument();
  });

  it("the enabled switch mutes a rule through PATCH", async () => {
    const calls = stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("switch", { name: "Failed runs is on" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ enabled: false }),
    );
    expect(await screen.findByRole("switch", { name: "Failed runs is off" })).toBeInTheDocument();
  });

  it("delete asks first, then deletes", async () => {
    const calls = stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Delete Failed runs" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Delete Failed runs" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.getAllByTestId("rule-row")).toHaveLength(1));
  });

  it("a reader sees rules but no controls and a count instead of destinations", async () => {
    const calls = stubAlertApi(false);
    mount(<AlertsPage />, READER);
    const rows = await screen.findAllByTestId("rule-row");
    expect(within(rows[0]!).getByText("2 destinations")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add a rule/ })).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(calls.some((c) => c.path.includes("destinations"))).toBe(false);
  });
});

describe("the rule form", () => {
  const open = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole("button", { name: "Add a rule" }));
    return screen.getByRole("form", { name: "New rule" });
  };

  it.each([
    ["run_failed", /Run ends as/, [/Severity or worse/, /Daily spend/, /expires/]],
    ["security_finding", /Severity or worse/, [/Run ends as/, /Daily spend/, /expires/]],
    ["approval_requested", /Agent/, [/Severity or worse/, /Run ends as/, /Daily spend/, /expires/]],
    ["approval_expiring", /before it expires/, [/Severity or worse/, /Run ends as/, /Daily spend/]],
    ["spend_over", /Daily spend limit/, [/Severity or worse/, /Run ends as/, /expires/]],
  ])("%s shows only the filters it needs", async (trigger, shown, hidden) => {
    stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const form = within(await open(user));
    await user.selectOptions(form.getByLabelText("Tell me when"), trigger);
    expect(form.getByText(shown)).toBeInTheDocument();
    for (const h of hidden) expect(form.queryByText(h)).toBeNull();
  });

  it("creates a spend rule with the right body, then shows the preview line", async () => {
    const calls = stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const form = within(await open(user));
    await user.selectOptions(form.getByLabelText("Tell me when"), "spend_over");
    await user.type(form.getByLabelText("Name"), "Daily spend");
    await user.type(form.getByLabelText(/Daily spend limit/), "50");
    await user.click(form.getByRole("checkbox", { name: /Ops webhook/ }));
    await user.clear(form.getByLabelText(/Cooldown/));
    await user.type(form.getByLabelText(/Cooldown/), "30");
    await user.click(form.getByRole("button", { name: "Create rule" }));
    const post = await waitFor(() => {
      const c = calls.find((x) => x.method === "POST" && x.path === "v1/alerts/rules");
      expect(c).toBeTruthy();
      return c!;
    });
    expect(post.body).toMatchObject({
      name: "Daily spend",
      trigger: "spend_over",
      project_id: null,
      filters: { threshold_usd: 50, period: "day" },
      destination_ids: ["dst_00000000000000000000000001"],
      cooldown_seconds: 1800,
      enabled: true,
    });
    expect(await screen.findByTestId("rule-saved")).toHaveTextContent("Saved Daily spend.");
    expect(await screen.findByTestId("preview")).toHaveTextContent(
      /would have fired \d+ times? in the last 7 days|once|no times/,
    );
  });

  it("checks required fields before asking the API", async () => {
    const calls = stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const form = within(await open(user));
    await user.selectOptions(form.getByLabelText("Tell me when"), "spend_over");
    await user.click(form.getByRole("button", { name: "Create rule" }));
    expect(form.getByText("Give the rule a name.")).toBeInTheDocument();
    expect(form.getByText("Pick at least one destination.")).toBeInTheDocument();
    expect(form.getByText(/amount in dollars/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("maps ALERT_NAME_TAKEN and RULE_INVALID to the field, and ALERT_LIMIT_REACHED to a banner", async () => {
    stubAlertApi(true);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const form = within(await open(user));
    await user.type(form.getByLabelText("Name"), "failed RUNS");
    await user.click(form.getByRole("checkbox", { name: /Ops webhook/ }));
    await user.click(form.getByRole("button", { name: "Create rule" }));
    expect(await form.findByText(/Another rule already has this name/)).toBeInTheDocument();
    await user.clear(form.getByLabelText("Name"));
    await user.type(form.getByLabelText("Name"), "Mine");
    await user.type(form.getByLabelText(/Agent/), "nobody");
    await user.click(form.getByRole("button", { name: "Create rule" }));
    expect(
      await form.findByText("No agent with that name was found in this scope."),
    ).toBeInTheDocument();
  });

  it("shows the limit and rate-limit sentences", async () => {
    let n = 0;
    stubAlertApi(true, (c) => {
      if (c.method !== "POST") return undefined;
      return n++ === 0
        ? errorBody("ALERT_LIMIT_REACHED", 409)
        : new Response(
            JSON.stringify({ error: { code: "RATE_LIMITED", message: "x", details: {} } }),
            { status: 429, headers: { "content-type": "application/json", "retry-after": "17" } },
          );
    });
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const form = within(await open(user));
    await user.type(form.getByLabelText("Name"), "Another");
    await user.click(form.getByRole("checkbox", { name: /Ops webhook/ }));
    await user.click(form.getByRole("button", { name: "Create rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/limit of 100 alert rules/);
    expect(screen.queryByText("API sentence that must not be shown")).toBeNull();
    await user.click(form.getByRole("button", { name: "Create rule" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Try again in 17 seconds"),
    );
  });

  it("words the busy answer of a write that waited for a delivery", () => {
    const busy = problemFrom(new ApiRequestError("x", 409, "ALERT_BUSY", null, true, {}), "rule");
    expect(busy.banner).toMatch(/being sent right now/);
  });

  it("editing keeps the trigger fixed and previews the saved rule; a timeout is worded gently", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("/preview") ? errorBody("ALERT_PREVIEW_TIMEOUT", 504) : undefined,
    );
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Edit Failed runs" }));
    const form = within(screen.getByRole("form", { name: "Edit rule" }));
    expect(form.getByLabelText(/^Tell me when/)).toBeDisabled();
    expect(await form.findByTestId("preview")).toHaveTextContent("took too long");
    expect(form.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("destinations", () => {
  it("lists masked targets and health in words; failing is amber and says how many failures", async () => {
    stubAlertApi(true);
    mount(<AlertsPage />, MANAGE);
    tab("destinations");
    cleanup();
    mount(<AlertsPage />, MANAGE);
    const rows = await screen.findAllByTestId("destination-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("https://hooks.example.com/…a91f")).toBeInTheDocument();
    expect(within(rows[0]!).getByTestId("health")).toHaveTextContent("OK");
    expect(within(rows[1]!).getByTestId("health")).toHaveTextContent("Failing");
    expect(within(rows[1]!).getByTestId("health")).toHaveAttribute("data-tone", "wait");
    expect(within(rows[1]!).getByText(/4 failures in a row/)).toBeInTheDocument();
  });

  it("Send test words the fixed error code and a success", async () => {
    const calls = stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Send test to Team Slack" }));
    expect(
      await screen.findByText(/Test failed\. The destination refused the alert/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send test to Ops webhook" }));
    expect(await screen.findByText(/Test sent\. The destination accepted it/)).toBeInTheDocument();
    // a JSON body, because the proxy refuses a POST without one
    expect(calls.find((c) => c.path.endsWith("/test"))?.body).toEqual({});
  });

  it("pause and resume use PATCH state", async () => {
    const calls = stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Pause Ops webhook" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ state: "paused" }),
    );
    expect(await screen.findByRole("button", { name: "Resume Ops webhook" })).toBeInTheDocument();
    expect(screen.getByText(/Paused by a person/)).toBeInTheDocument();
  });

  it("create shows the signing secret once, keeps it out of storage and the query cache, and clears it on Done", async () => {
    stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    const { client } = mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const form = within(screen.getByRole("form", { name: "New destination" }));
    await user.type(form.getByLabelText("Name"), "Pager");
    await user.type(form.getByLabelText(/^Webhook URL/), "https://pager.example.com/hook/abc123");
    await user.click(form.getByRole("button", { name: "Add destination" }));
    const panel = await screen.findByTestId("secret-panel");
    const secret = within(panel).getByTestId("signing-secret").textContent!;
    expect(secret).toMatch(/^whsec_/);
    // never in storage, the URL, or the React Query caches
    expect(JSON.stringify({ ...localStorage })).not.toContain(secret);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(secret);
    expect(window.location.href).not.toContain(secret);
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((m) => [m.state, m.options.mutationKey]),
      ),
    ).not.toContain(secret);
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
      ),
    ).not.toContain(secret);
    // the listing does not carry it, and the form no longer holds the URL
    await waitFor(() => expect(screen.getAllByTestId("destination-row")).toHaveLength(3));
    const done = within(panel).getByRole("button", { name: "Done" });
    expect(done).toBeDisabled();
    await user.click(within(panel).getByRole("checkbox", { name: /stored the secret/ }));
    await user.click(done);
    expect(screen.queryByTestId("secret-panel")).toBeNull();
    expect(document.body.textContent).not.toContain(secret);
  });

  it("the mutation cache does not keep the target URL a destination was saved with", async () => {
    stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    const { client } = mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const form = within(screen.getByRole("form", { name: "New destination" }));
    await user.type(form.getByLabelText("Name"), "Pager");
    await user.type(form.getByLabelText(/^Webhook URL/), "https://pager.example.com/hook/abc123");
    await user.click(form.getByRole("button", { name: "Add destination" }));
    await screen.findByTestId("secret-panel");
    // gcTime 0: the mutation (and the variables, which hold the URL) leaves the cache once done
    await waitFor(() =>
      expect(
        JSON.stringify(
          client
            .getMutationCache()
            .getAll()
            .map((m) => m.state.variables),
        ),
      ).not.toContain("hook/abc123"),
    );
  });

  it("rotate asks first and shows the new secret in the same panel", async () => {
    stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Rotate secret of Ops webhook" }));
    expect(screen.queryByTestId("secret-panel")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Rotate Ops webhook" }));
    expect(await screen.findByTestId("signing-secret")).toHaveTextContent(/^whsec_fixture_rotated/);
    expect(screen.queryByRole("button", { name: /Rotate secret of Team Slack/ })).toBeNull();
  });

  it("email is disabled with an explanation when the server has no mail set up", async () => {
    stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    expect(screen.getByRole("option", { name: /Email \(not set up/ })).toBeDisabled();
    expect(screen.getByTestId("email-unavailable")).toHaveTextContent(/outgoing mail/);
  });

  it("maps DESTINATION_TARGET_INVALID reasons to words and keeps the typed name", async () => {
    stubAlertApi(true);
    tab("destinations");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const form = within(screen.getByRole("form", { name: "New destination" }));
    await user.type(form.getByLabelText("Name"), "Plain");
    await user.type(form.getByLabelText(/^Webhook URL/), "http://example.com/x");
    await user.click(form.getByRole("button", { name: "Add destination" }));
    expect(await form.findByText("The address must start with https://.")).toBeInTheDocument();
    await user.clear(form.getByLabelText(/^Webhook URL/));
    await user.type(form.getByLabelText(/^Webhook URL/), "https://db.internal/x");
    await user.click(form.getByRole("button", { name: "Add destination" }));
    expect(
      await form.findByText("Addresses on an internal network are not allowed."),
    ).toBeInTheDocument();
    await user.clear(form.getByLabelText(/^Webhook URL/));
    await user.type(form.getByLabelText(/^Webhook URL/), "https://hooks.example.com:6379/x");
    await user.click(form.getByRole("button", { name: "Add destination" }));
    expect(await form.findByText(/That port is not allowed/)).toBeInTheDocument();
    expect(form.getByLabelText("Name")).toHaveValue("Plain");
  });

  it("other roles get a plain explanation and the destinations list is never requested", async () => {
    const calls = stubAlertApi(false);
    tab("destinations");
    mount(<AlertsPage />, READER);
    expect(await screen.findByTestId("destinations-forbidden")).toHaveTextContent(
      /owners and admins/,
    );
    expect(calls.some((c) => c.path.endsWith("alerts/destinations"))).toBe(false);
  });

  it("a 403 from the API is the same explanation", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("alerts/destinations") ? errorBody("PERMISSION_DENIED", 403) : undefined,
    );
    tab("destinations");
    mount(<AlertsPage />, MANAGE);
    expect(await screen.findByTestId("destinations-forbidden")).toBeInTheDocument();
  });
});

describe("history", () => {
  it("renders status as glyph and word, severity, detail words, and links where ids exist", async () => {
    stubAlertApi(true);
    tab("history");
    mount(<AlertsPage />, MANAGE);
    const rows = await screen.findAllByTestId("history-row");
    expect(rows).toHaveLength(11);
    expect(within(rows[0]!).getByText("Delivered")).toBeInTheDocument();
    expect(within(rows[0]!).getByRole("link", { name: "Run" })).toHaveAttribute(
      "href",
      "/w/demo/projects/demo/runs/run_00000000000000000000000001",
    );
    // pending with attempts: retrying, with the next try, in words
    expect(within(rows[1]!).getByText("Retrying (attempt 3)")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/destination had an error of its own/)).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/Next try 2026-10-12 15:00 UTC/)).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Gave up")).toBeInTheDocument();
    expect(within(rows[2]!).getByText(/refused the alert/)).toBeInTheDocument();
    expect(within(rows[3]!).getByText(/rule's cooldown was still running/)).toBeInTheDocument();
    expect(within(rows[4]!).getByText("Critical")).toBeInTheDocument();
    expect(within(rows[4]!).getByRole("link", { name: "Finding" }).getAttribute("href")).toContain(
      "?finding=fnd_",
    );
    expect(within(rows[5]!).getByText("Rule deleted")).toBeInTheDocument();
    expect(within(rows[6]!).getByRole("link", { name: "Approval" })).toHaveAttribute(
      "href",
      "/w/demo/approvals?approval=apr_00000000000000000000000001",
    );
    expect(within(rows[10]!).getByText("Destination deleted")).toBeInTheDocument();
  });

  it("does not show a next try for a terminal row", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("alerts/notifications")
        ? Response.json({
            items: [
              {
                id: "alt_1",
                rule: null,
                destination: null,
                trigger: "run_failed",
                severity: "high",
                status: "dead",
                skip_reason: null,
                attempts: 6,
                error_code: "timeout",
                project_id: null,
                run_id: null,
                finding_id: null,
                approval_id: null,
                agent_slug: null,
                created_at: "2026-10-12T10:00:00Z",
                delivered_at: null,
                next_attempt_at: "2026-10-12T11:00:00Z",
              },
            ],
            next_cursor: null,
          })
        : undefined,
    );
    tab("history");
    mount(<AlertsPage />, MANAGE);
    const row = await screen.findByTestId("history-row");
    expect(row).not.toHaveTextContent("Next try");
    expect(row).toHaveTextContent("did not answer in time");
  });

  it("filters go to the API, load more follows the cursor, and the empty filter result can be cleared", async () => {
    const calls = stubAlertApi(true);
    tab("history");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await screen.findAllByTestId("history-row");
    await user.selectOptions(screen.getByLabelText("Status"), "skipped");
    await waitFor(() => expect(calls.at(-1)!.search).toContain("status=skipped"));
    await waitFor(() => expect(screen.getAllByTestId("history-row")).toHaveLength(3));
    await user.selectOptions(screen.getByLabelText("Trigger"), "spend_over");
    expect(await screen.findByTestId("history-empty")).toHaveTextContent(
      "No alerts match these filters",
    );
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.getAllByTestId("history-row")).toHaveLength(11));
    await user.selectOptions(
      screen.getByLabelText("Destination"),
      "dst_00000000000000000000000002",
    );
    await waitFor(() => expect(calls.at(-1)!.search).toContain("destination_id=dst_"));
  });

  it("pages with Load more", async () => {
    const calls = stubAlertApi(true);
    tab("history");
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await screen.findAllByTestId("history-row");
    // 11 rows fit one page of 25: the end is stated, no button
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(screen.getByText("End of list")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("notifications"))!.search).toContain("limit=25");
    void user;
  });

  it("a reader sees the destination type but not its name, no destination filter and no approval link", async () => {
    stubAlertApi(false);
    tab("history");
    mount(<AlertsPage />, READER);
    const rows = await screen.findAllByTestId("history-row");
    expect(within(rows[0]!).getByText("Hidden for your role")).toBeInTheDocument();
    expect(screen.queryByLabelText("Destination")).toBeNull();
    expect(screen.queryByRole("link", { name: "Approval" })).toBeNull();
  });

  it("an empty history says so", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("alerts/notifications")
        ? Response.json({ items: [], next_cursor: null })
        : undefined,
    );
    tab("history");
    mount(<AlertsPage />, MANAGE);
    expect(await screen.findByTestId("history-empty")).toHaveTextContent(
      "Nothing has been sent yet",
    );
  });
});

describe("the guided empty state", () => {
  const none = (c: { path: string }) =>
    c.path.endsWith("alerts/rules")
      ? Response.json({ items: [] })
      : c.path.endsWith("alerts/destinations")
        ? Response.json({ items: [], email_configured: false })
        : undefined;

  it("says: add a destination, then a rule, and the button opens the destination form", async () => {
    stubAlertApi(true, none);
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    const empty = await screen.findByTestId("alerts-empty");
    expect(empty).toHaveTextContent("Add a destination, then a rule");
    await user.click(within(empty).getByRole("button", { name: "Add a destination" }));
    expect(url.search).toBe("tab=destinations");
    expect(await screen.findByRole("form", { name: "New destination" })).toBeInTheDocument();
  });

  it("with a destination but no rule, the starter opens the rule form prefilled", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("alerts/rules") ? Response.json({ items: [] }) : undefined,
    );
    const user = userEvent.setup();
    mount(<AlertsPage />, MANAGE);
    await user.click(await screen.findByRole("button", { name: /Start with/ }));
    const form = within(screen.getByRole("form", { name: "New rule" }));
    expect(form.getByLabelText("Name")).toHaveValue("Failed runs");
    expect(form.getByLabelText("Tell me when")).toHaveValue("run_failed");
  });

  it("a reader is told who can add rules", async () => {
    stubAlertApi(false, (c) =>
      c.path.endsWith("alerts/rules") ? Response.json({ items: [] }) : undefined,
    );
    mount(<AlertsPage />, READER);
    expect(await screen.findByTestId("alerts-empty")).toHaveTextContent(
      /Owners and admins can add rules/,
    );
  });
});

describe("page access", () => {
  it("without alert.read the page is a plain explanation", () => {
    const calls = stubAlertApi(false);
    mount(<AlertsPage />, ["run.read"]);
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("text from the API is rendered as text, never as markup", async () => {
    stubAlertApi(true, (c) =>
      c.path.endsWith("alerts/rules")
        ? Response.json({
            items: [
              {
                id: "rul_1",
                name: '<img src=x onerror="alert(1)">',
                trigger: "run_failed",
                project_id: null,
                filters: { agent: "<b>bold</b>" },
                cooldown_seconds: 0,
                enabled: true,
                last_fired_at: null,
                destination_count: 0,
                destinations: [],
              },
            ],
          })
        : undefined,
    );
    const { container } = mount(<AlertsPage />, MANAGE);
    await screen.findByTestId("rule-row");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  });
});

describe("alerts vocabulary", () => {
  it("maps every fixed delivery code to words and never echoes an unknown one", () => {
    for (const c of [
      "timeout",
      "connect_failed",
      "dns_failed",
      "tls_failed",
      "blocked_address",
      "blocked_port",
      "http_3xx",
      "http_4xx",
      "http_5xx",
      "bad_response",
      "smtp_failed",
      "config_unreadable",
      "internal_error",
    ])
      expect(errorCodeWords(c)).toMatch(/\w+ \w+/);
    expect(errorCodeWords("<script>")).not.toContain("script");
    expect(errorCodeWords(null)).toBe("");
  });
  it("status and health words", () => {
    expect(notificationStatus({ status: "pending", attempts: 0 }).word).toBe("Waiting to send");
    expect(notificationStatus({ status: "dead", attempts: 6 }).tone).toBe("wait");
    expect(
      destinationHealth({ state: "paused", paused_reason: "failures", consecutive_failures: 10 })
        .detail,
    ).toMatch(/10 failures/);
  });
  it("filters in words and request bodies per trigger", () => {
    expect(
      filterWords(
        { trigger: "spend_over", project_id: null, filters: { threshold_usd: 50 } },
        () => "",
      ),
    ).toEqual(["in any project", "over $50.00 in a UTC day"]);
    const ok = buildRuleBody(
      newDraft({
        name: " x ",
        trigger: "approval_expiring",
        leadMinutes: "15",
        destinationIds: ["dst_1"],
      }),
    );
    expect(ok).toMatchObject({
      ok: true,
      body: { name: "x", filters: { expiring_lead_seconds: 900 } },
    });
    const bad = buildRuleBody(newDraft({ trigger: "approval_expiring", leadMinutes: "0" }));
    expect(bad.ok).toBe(false);
  });
  it("problemFrom ignores unrelated errors", () => {
    expect(problemFrom(new Error("x"), "rule")).toEqual({ fields: {}, banner: null });
    expect(
      problemFrom(
        new ApiRequestError("m", 422, "RULE_INVALID", null, false, {
          field: "threshold_usd",
          reason: "too_small",
        }),
        "rule",
      ).fields.threshold_usd,
    ).toMatch(/too small/);
  });
});
