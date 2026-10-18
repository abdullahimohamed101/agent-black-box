import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuotasSettings } from "@/components/QuotasSettings";
import { SettingsNav } from "@/components/SettingsNav";
import { SettingsPage } from "@/components/SettingsPage";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { fixtureQuotas, resetFixtureQuotas } from "@/fixtures/quotas";
import type { WorkspaceContext } from "@/lib/workspace";

vi.mock("next/navigation", () => ({ usePathname: () => "/w/acme/settings/quotas" }));

const PRJ = "prj_00000000000000000000000001";
const ctx = (permissions: string[]): WorkspaceContext => ({
  mode: "session",
  user: { id: "u", email: "me@x.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "acme", name: "Acme" },
  role: "OWNER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [{ id: PRJ, slug: "demo", name: "Demo project" }],
});
const mount = (permissions = ["project.read", "project.write", "audit.read"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SettingsPage needs="project.read" what="project quotas">
          <QuotasSettings />
        </SettingsPage>
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
};
const puts: unknown[] = [];
function api(options: { fail?: Response; canWrite?: boolean; status?: number } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      const u = new URL(r.url);
      if (!u.pathname.endsWith("/quotas"))
        return Response.json({ error: { code: "NOPE", message: "no" } }, { status: 404 });
      if (options.status)
        return Response.json(
          { error: { code: "X", message: "It broke.", category: "INTERNAL", request_id: "req_9" } },
          { status: options.status },
        );
      let body: unknown;
      if (r.method === "PUT") {
        body = await r.json();
        puts.push(body);
        if (options.fail) return options.fail.clone();
      }
      const reply = fixtureQuotas(PRJ, body, options.canWrite ?? true);
      return Response.json(reply.body, { status: reply.status });
    }),
  );
}
beforeEach(() => {
  puts.length = 0;
  resetFixtureQuotas();
});
afterEach(() => vi.unstubAllGlobals());

describe("the usage list", () => {
  it("shows each quota with figures, a status word and, at a limit, what is refused", async () => {
    api();
    mount();
    const items = await screen.findAllByTestId("quota-item");
    expect(items.map((i) => i.getAttribute("data-quota"))).toEqual([
      "agents",
      "runs_per_day",
      "events_per_day",
      "artifact_bytes",
    ]);
    expect(within(items[0]!).getByText("12 of 500 agents")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Within limit")).toBeInTheDocument();
    expect(within(items[1]!).getByText("41,200 of 50,000 runs today")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Near limit")).toBeInTheDocument();
    expect(within(items[1]!).queryByTestId("quota-refusing")).toBeNull();
    expect(within(items[2]!).getByText("Limit reached")).toBeInTheDocument();
    expect(within(items[2]!).getByTestId("quota-refusing")).toHaveTextContent(
      /refused until midnight UTC/,
    );
    expect(within(items[3]!).getByText("1.25 GiB of 5 GiB stored")).toBeInTheDocument();
  });

  it("states the status in words, not only by colour, and hides the decorative glyph", async () => {
    api();
    mount();
    const items = await screen.findAllByTestId("quota-item");
    for (const item of items) {
      const glyph = item.querySelector(".quota-glyph");
      expect(glyph).toHaveAttribute("aria-hidden", "true");
      expect(item.querySelector(".quota-status")?.textContent).toMatch(
        /Within limit|Near limit|Limit reached/,
      );
      expect(item.querySelector(".quota-bar")).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("shows a read-only note, and no inputs, without project.write", async () => {
    api();
    mount(["project.read"]);
    await screen.findAllByTestId("quota-item");
    expect(screen.getByTestId("quota-readonly")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save limits" })).toBeNull();
    expect(screen.queryByLabelText(/Distinct agents/)).toBeNull();
  });

  it("is not available without project.read", () => {
    api();
    mount([]);
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
  });

  it("shows a plain loading state, then a plain error with a way to retry", async () => {
    api({ status: 500 });
    mount();
    expect(screen.getByText(/Loading quotas/)).toBeInTheDocument();
    expect(await screen.findByText("Could not load this data")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says plainly when quotas are switched off", async () => {
    api();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementationOnce(async () => {
      const reply = fixtureQuotas(PRJ, undefined, true);
      return Response.json({ ...(reply.body as object), enforced: false });
    });
    mount();
    expect(await screen.findByText(/switched off on this installation/)).toBeInTheDocument();
  });
});

describe("overrides", () => {
  it("saves only what changed, shows what the API stored, and confirms in words", async () => {
    api();
    const user = userEvent.setup();
    mount();
    const save = await screen.findByRole("button", { name: "Save limits" });
    expect(save).toBeDisabled();
    const events = screen.getByLabelText("Events per day");
    expect(events).toHaveAttribute("placeholder", "Default 5,000,000");
    await user.type(events, "9000000");
    expect(save).toBeEnabled();
    await user.click(save);
    expect(puts).toEqual([{ events_per_day: 9_000_000 }]);
    expect(
      await screen.findByText(/Saved\. The new limits apply to the next request/),
    ).toBeInTheDocument();
    const item = screen
      .getAllByTestId("quota-item")
      .find((i) => i.getAttribute("data-quota") === "events_per_day")!;
    expect(within(item).getByText("5,000,000 of 9,000,000 events today")).toBeInTheDocument();
    expect(within(item).getByText("Within limit")).toBeInTheDocument();
    expect(within(item).getByText(/Overridden for this project/)).toBeInTheDocument();
    expect(within(item).queryByTestId("quota-refusing")).toBeNull();
  });

  it("types storage in MiB and sends bytes", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await user.type(await screen.findByLabelText("Artifact storage (MiB)"), "2048");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(puts).toEqual([{ artifact_bytes: 2048 * 1024 ** 2 }]));
  });

  it("clearing an override sends null", async () => {
    api();
    const user = userEvent.setup();
    mount();
    const agents = await screen.findByLabelText("Distinct agents");
    await user.type(agents, "40");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    await screen.findByText(/Saved\./);
    const again = screen.getByLabelText("Distinct agents");
    expect(again).toHaveValue("40");
    await user.clear(again);
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(puts).toEqual([{ agents: 40 }, { agents: null }]));
    await waitFor(() => expect(screen.getByLabelText("Distinct agents")).toHaveValue(""));
  });

  it("refuses an invalid value before sending, names the field and does not echo the text", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await user.type(await screen.findByLabelText("Distinct agents"), "zero");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/whole number/);
    expect(alert.textContent).not.toContain("zero");
    expect(screen.getByLabelText("Distinct agents")).toHaveAttribute("aria-invalid", "true");
    expect(puts).toEqual([]);
  });

  it("shows the API's refusal as a banner and keeps what was typed", async () => {
    api({
      fail: Response.json(
        {
          error: {
            code: "QUOTA_SETTINGS_INVALID",
            message: "The agents quota must be a whole number from 1 to 1000000.",
            category: "VALIDATION",
            request_id: "req_7",
          },
        },
        { status: 422 },
      ),
    });
    const user = userEvent.setup();
    mount();
    await user.type(await screen.findByLabelText("Distinct agents"), "50");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    expect(await screen.findByText(/must be a whole number from 1 to 1000000/)).toBeInTheDocument();
    expect(screen.getByLabelText("Distinct agents")).toHaveValue("50");
  });

  it("shows a 403 as a clean role message, not a crash", async () => {
    api({
      fail: Response.json(
        {
          error: { code: "PERMISSION_DENIED", message: "Not allowed.", category: "AUTHORIZATION" },
        },
        { status: 403 },
      ),
    });
    const user = userEvent.setup();
    mount();
    await user.type(await screen.findByLabelText("Distinct agents"), "50");
    await user.click(screen.getByRole("button", { name: "Save limits" }));
    expect(await screen.findByText(/Your role does not allow this/)).toBeInTheDocument();
  });

  it("is operable from the keyboard alone", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("quota-item");
    await user.tab(); // the audit-log link in the intro
    expect(screen.getByRole("link", { name: "audit log" })).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("Project")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText("Distinct agents")).toHaveFocus();
    await user.keyboard("42");
    await user.tab(); // runs, events, storage, then the button
    await user.tab();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "Save limits" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(puts).toEqual([{ agents: 42 }]));
  });
});

describe("the settings tab", () => {
  it("is listed for anyone who can read projects", () => {
    const render1 = (permissions: string[]) =>
      render(
        <WorkspaceProvider value={ctx(permissions)}>
          <SettingsNav />
        </WorkspaceProvider>,
      );
    const { unmount } = render1(["project.read"]);
    expect(screen.getByRole("link", { name: "Quotas" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "Capture and detectors" })).toBeNull();
    unmount();
    render1(["project.read", "project.write"]);
    expect(screen.getByRole("link", { name: "Capture and detectors" })).toBeInTheDocument();
  });
});
