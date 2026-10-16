import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RetentionSettings } from "@/components/RetentionSettings";
import { SettingsNav } from "@/components/SettingsNav";
import { SettingsPage } from "@/components/SettingsPage";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { fixtureRetention, resetFixtureRetention } from "@/fixtures/retention";
import type { WorkspaceContext } from "@/lib/workspace";

vi.mock("next/navigation", () => ({ usePathname: () => "/w/acme/settings/retention" }));

const WS = "ws_00000000000000000000000001";
const ctx = (permissions: string[]): WorkspaceContext => ({
  mode: "session",
  user: { id: "u", email: "me@x.co", name: null },
  workspace: { id: WS, slug: "acme", name: "Acme" },
  role: "OWNER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [],
});
const OWNER = ["retention.read", "retention.write", "retention.audit.write", "audit.read"];
const mount = (permissions = OWNER) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SettingsPage needs="retention.read" what="retention settings">
          <RetentionSettings />
        </SettingsPage>
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
};

type Options = {
  mode?: "off" | "dry_run" | "enforce";
  canWrite?: boolean;
  failPolicy?: number;
  failPut?: Response;
  failPreview?: Response;
  reports?: unknown[];
};
const calls: { method: string; path: string; search: string; body?: unknown }[] = [];
function api(options: Options = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      const u = new URL(r.url);
      const rest = u.pathname.split("/retention")[1]?.replace(/^\//, "") || undefined;
      let body: unknown;
      if (r.method === "PUT") body = await r.json();
      calls.push({ method: r.method, path: u.pathname, search: u.search, body });
      if (options.failPolicy && !rest && r.method === "GET")
        return Response.json(
          { error: { code: "X", message: "It broke.", category: "INTERNAL", request_id: "req_9" } },
          { status: options.failPolicy },
        );
      if (r.method === "PUT" && options.failPut) return options.failPut.clone();
      if (rest === "preview" && options.failPreview) return options.failPreview.clone();
      const reply = fixtureRetention(WS, rest, u.searchParams, body, options.canWrite ?? true);
      let out = reply.body as Record<string, unknown>;
      if (!rest && options.mode && reply.status === 200) out = { ...out, mode: options.mode };
      if (rest === "reports" && options.reports) out = { items: options.reports };
      return Response.json(out, { status: reply.status });
    }),
  );
}
const envelope = (status: number, code: string, message: string, headers = {}) =>
  Response.json(
    { error: { code, message, category: "X", retryable: status >= 500, request_id: "req_1" } },
    { status, headers },
  );
beforeEach(() => {
  calls.length = 0;
  resetFixtureRetention();
});
afterEach(() => vi.unstubAllGlobals());

const tier = (name: string) =>
  screen.getAllByTestId("retention-tier").find((t) => t.getAttribute("data-tier") === name)!;
const puts = () => calls.filter((c) => c.method === "PUT");

describe("the four tiers", () => {
  it("shows each tier with its value, where it comes from and what it removes", async () => {
    api();
    mount();
    await screen.findAllByTestId("retention-tier");
    expect(screen.getAllByTestId("retention-tier").map((t) => t.getAttribute("data-tier"))).toEqual(
      ["artifacts", "trace", "run", "audit"],
    );
    expect(within(tier("artifacts")).getByTestId("tier-value")).toHaveTextContent(
      "30 days (installation default)",
    );
    expect(tier("artifacts")).toHaveTextContent("Terminal output and large payload bodies");
    expect(tier("trace")).toHaveTextContent("Events, spans, findings and evaluations");
    expect(tier("run")).toHaveTextContent("The run summary and its numbers");
    expect(tier("audit")).toHaveTextContent("Audit log entries older than this");
  });

  it("shows 'Keep forever' and 'set for this workspace' after a workspace choice", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "forever");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await waitFor(() =>
      expect(within(tier("audit")).getByTestId("tier-value")).toHaveTextContent(
        "Keep forever (set for this workspace)",
      ),
    );
  });

  it("is read-only without retention.write: values and an explanation, no controls", async () => {
    api();
    mount(["retention.read"]);
    await screen.findAllByTestId("retention-tier");
    expect(screen.getByTestId("retention-readonly")).toHaveTextContent("Owners and admins");
    expect(screen.queryByRole("button", { name: "Save periods" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Preview/ })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(within(tier("trace")).getByTestId("tier-value")).toHaveTextContent("90 days");
  });

  it("is not available without retention.read, and the tab only shows with it", () => {
    api();
    mount([]);
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
    const { unmount } = render(
      <WorkspaceProvider value={ctx(["retention.read"])}>
        <SettingsNav />
      </WorkspaceProvider>,
    );
    expect(screen.getByRole("link", { name: "Retention" })).toHaveAttribute(
      "href",
      "/w/acme/settings/retention",
    );
    unmount();
    render(
      <WorkspaceProvider value={ctx(["member.read"])}>
        <SettingsNav />
      </WorkspaceProvider>,
    );
    expect(screen.queryByRole("link", { name: "Retention" })).toBeNull();
  });
});

describe("the mode banner", () => {
  it.each([
    ["dry_run", "Retention is in dry-run mode: nothing is being deleted"],
    ["enforce", "Retention is enforcing"],
    ["off", "Retention is off"],
  ] as const)("says %s in words", async (mode, text) => {
    api({ mode });
    mount();
    const banner = await screen.findByTestId("retention-mode");
    expect(banner).toHaveAttribute("data-mode", mode);
    expect(banner).toHaveTextContent(text);
    expect(banner.querySelector(".retention-glyph")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("editing", () => {
  it("saves only what changed and shows what the API stored", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    const save = screen.getByRole("button", { name: "Save periods" });
    expect(save).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Trace detail period"), "days");
    await user.type(screen.getByLabelText("Trace detail days"), "120");
    await user.click(save);
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]!.body).toEqual({ trace: 120 });
    expect(await screen.findByTestId("save-status")).toHaveTextContent("Saved.");
    expect(within(tier("trace")).getByTestId("tier-value")).toHaveTextContent(
      "120 days (set for this workspace)",
    );
  });

  it("returns a tier to the installation default with null", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "forever");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await screen.findByText(/Keep forever \(set for this workspace\)/);
    await user.selectOptions(screen.getByLabelText("Audit log period"), "default");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    // forever -> the 365-day default is a shortening, so it asks first
    await user.click(await screen.findByRole("button", { name: "Save and shorten" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]!.body).toEqual({ audit: null });
  });

  it("refuses a value below the floor or malformed without a request or an echo", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    const days = screen.getByLabelText("Artifacts days");
    await user.type(days, "3");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("at least 7 days");
    expect(alert).not.toHaveTextContent(/\b3\b/);
    await user.clear(days);
    await user.type(days, "1e3");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("whole number");
    expect(puts()).toHaveLength(0);
  });

  it("explains the order rule when artifacts would outlast trace detail", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "forever");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "cannot be kept longer than trace detail",
    );
    expect(puts()).toHaveLength(0);
  });

  it("shows the API's policy error in words when the server refuses", async () => {
    api({
      failPut: envelope(
        422,
        "RETENTION_POLICY_INVALID",
        "Retention must be at least 7 days (or forever).",
      ),
    });
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "forever");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 7 days");
  });

  it("maps a rate limit with Retry-After, and a 404, to words", async () => {
    api({ failPut: envelope(429, "RATE_LIMITED", "Slow down.", { "retry-after": "17" }) });
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "forever");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many requests for this workspace. Try again in 17 seconds.",
    );
    vi.unstubAllGlobals();
    api({ failPut: envelope(404, "NOT_FOUND", "Workspace not found.") });
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("workspace was not found"),
    );
  });
});

describe("shortening asks first", () => {
  it("asks, in plain words about permanent deletion, before a shorter period is saved", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    await user.type(screen.getByLabelText("Artifacts days"), "14");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    const confirm = await screen.findByTestId("retention-confirm");
    expect(puts()).toHaveLength(0);
    expect(confirm).toHaveTextContent("This shortens how long data is kept.");
    expect(confirm).toHaveTextContent("Artifacts: from 30 days to 14 days");
    expect(confirm).toHaveTextContent("deleted permanently and cannot be restored");
    expect(confirm).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Save and shorten" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]!.body).toEqual({ artifacts: 14 });
    expect(screen.queryByTestId("retention-confirm")).toBeNull();
  });

  it("Cancel keeps everything as it was; editing again closes the question", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    await user.type(screen.getByLabelText("Artifacts days"), "14");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await user.click(
      within(await screen.findByTestId("retention-confirm")).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(screen.queryByTestId("retention-confirm")).toBeNull();
    expect(puts()).toHaveLength(0);
  });

  it("does not ask when a period gets longer", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Trace detail period"), "days");
    await user.type(screen.getByLabelText("Trace detail days"), "120");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(screen.queryByTestId("retention-confirm")).toBeNull();
  });

  it("says deletion is happening now when the installation is enforcing", async () => {
    api({ mode: "enforce" });
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    await user.type(screen.getByLabelText("Artifacts days"), "14");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByTestId("retention-confirm")).toHaveTextContent(
      "The retention job is enforcing, so data older than the new period will be deleted permanently",
    );
  });
});

describe("preview", () => {
  it("asks the API about the PROPOSED policy and says what would go, and that nothing is deleted yet in dry run", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Trace detail period"), "days");
    await user.type(screen.getByLabelText("Trace detail days"), "30");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    const result = await screen.findByTestId("retention-preview");
    const asked = calls.find((c) => c.path.endsWith("/preview"))!;
    expect(new URLSearchParams(asked.search).get("trace_days")).toBe("30");
    expect(new URLSearchParams(asked.search).get("artifacts_days")).toBe("30"); // the effective default
    expect(result).toHaveTextContent(
      "This would remove 210 runs, 2,520 events and 630 MiB of artifacts.",
    );
    expect(result).toHaveTextContent("dry-run mode, so nothing is deleted yet");
    expect(result).toHaveTextContent("Nothing was saved");
    expect(puts()).toHaveLength(0);
  });

  it("drops a stale preview when the proposal changes", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    await screen.findByTestId("retention-preview");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "forever");
    expect(screen.queryByTestId("retention-preview")).toBeNull();
  });

  it("maps a timeout to words with Try again, which asks again", async () => {
    api({ failPreview: envelope(504, "RETENTION_PREVIEW_TIMEOUT", "Too slow.") });
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    const problem = await screen.findByTestId("preview-error");
    expect(problem).toHaveTextContent("The preview took too long");
    vi.unstubAllGlobals();
    api();
    await user.click(within(problem).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("retention-preview")).toBeInTheDocument();
  });

  it("does not ask while the proposal is invalid", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    await user.type(screen.getByLabelText("Artifacts days"), "2");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 7 days");
    expect(calls.some((c) => c.path.endsWith("/preview"))).toBe(false);
  });
});

describe("last passes", () => {
  it("lists reports with the mode in words, counts, and a truncation note", async () => {
    api();
    mount();
    const passes = await screen.findAllByTestId("retention-pass");
    expect(passes).toHaveLength(4);
    expect(passes[0]).toHaveTextContent("Trace detail");
    expect(passes[0]).toHaveTextContent("Dry run");
    expect(passes[0]).toHaveTextContent("would remove 18 runs, 216 events");
    expect(passes[0]).not.toHaveTextContent("Stopped early");
    expect(passes[1]).toHaveTextContent(
      "Stopped early at a limit, so these figures are a lower bound.",
    );
  });

  it("says Enforced and 'removed' for an enforced pass", async () => {
    api({
      reports: [
        { id: 9, tier: "run", mode: "enforce", runs: 4, events: 0, artifact_bytes: 0, rows: 8, truncated: false, cutoff_at: null, started_at: "2026-10-15T03:00:00Z", finished_at: "2026-10-15T03:00:04Z" },
      ],
    }); // prettier-ignore
    mount();
    const pass = await screen.findByTestId("retention-pass");
    expect(pass).toHaveTextContent("Enforced");
    expect(pass).toHaveTextContent("removed 4 runs");
    expect(pass).not.toHaveTextContent("would remove");
  });

  it("has an empty state", async () => {
    api({ reports: [] });
    mount();
    expect(await screen.findByTestId("no-passes")).toHaveTextContent("No passes yet");
  });
});

describe("loading and errors", () => {
  it("shows a loading state, then an error with Try again", async () => {
    api({ failPolicy: 500 });
    mount();
    expect(screen.getByText(/Loading the retention policy/)).toBeInTheDocument();
    expect(await screen.findByText("Could not load this data")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("renders every string as text, never as markup", async () => {
    api({
      reports: [
        { id: 1, tier: "run", mode: "dry_run", runs: 0, events: 0, artifact_bytes: 0, rows: 0, truncated: false, cutoff_at: null, started_at: "2026-10-15T03:00:00Z", finished_at: "2026-10-15T03:00:04Z" },
      ],
    }); // prettier-ignore
    const { container } = mount();
    await screen.findByTestId("retention-pass");
    expect(container.querySelector("script, img, iframe")).toBeNull();
  });
});

describe("the grace period before a shortening applies", () => {
  it("tells the person, before saving, that the longer period still applies for a while", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Artifacts period"), "days");
    await user.type(screen.getByLabelText("Artifacts days"), "14");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    expect(await screen.findByTestId("grace-note")).toHaveTextContent(
      "takes effect 24 hours after you save; until then the longer period still applies",
    );
  });

  it("shows the effective date and the period still in force, and cancels by saving it", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Trace detail period"), "days");
    await user.type(screen.getByLabelText("Trace detail days"), "60");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    await user.click(await screen.findByRole("button", { name: "Save and shorten" }));
    const pending = await within(tier("trace")).findByTestId("tier-pending");
    expect(pending).toHaveTextContent(
      "This shortening takes effect on 2026-10-16 09:30 UTC; until then the longer period (90 days) still applies.",
    );
    expect(await screen.findByTestId("save-status")).toHaveTextContent("takes effect after");
    await user.click(within(pending).getByRole("button", { name: "Cancel this shortening" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]!.body).toEqual({ trace: 90 }); // the period in force: a lengthening, immediate
    await waitFor(() => expect(within(tier("trace")).queryByTestId("tier-pending")).toBeNull());
  });

  it("shows both periods in a preview of a shortening", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Trace detail period"), "days");
    await user.type(screen.getByLabelText("Trace detail days"), "60");
    await user.click(screen.getByRole("button", { name: /Preview/ }));
    const preview = await screen.findByTestId("retention-preview");
    expect(
      within(preview).getByText(/Takes effect on 2026-10-16 09:30 UTC; until then/),
    ).toBeInTheDocument();
  });
});

describe("the audit log tier", () => {
  it("is read-only for an admin, with a plain explanation and the minimum", async () => {
    api();
    mount(["retention.read", "retention.write", "audit.read"]);
    await screen.findAllByTestId("retention-tier");
    expect(screen.queryByLabelText("Audit log period")).toBeNull();
    expect(screen.getByLabelText("Trace detail period")).toBeInTheDocument();
    expect(within(tier("audit")).getByTestId("audit-owner-only")).toHaveTextContent(
      "Only an owner can change how long the audit log is kept",
    );
    expect(within(tier("audit")).getByTestId("tier-value")).toHaveTextContent("365 days");
  });

  it("holds the audit log to its own minimum, without echoing the value", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findAllByTestId("retention-tier");
    await user.selectOptions(screen.getByLabelText("Audit log period"), "days");
    await user.type(screen.getByLabelText("Audit log days"), "30");
    await user.click(screen.getByRole("button", { name: "Save periods" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("at least 90 days");
    expect(puts()).toHaveLength(0);
  });
});
