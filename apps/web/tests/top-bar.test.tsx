import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/AppShell";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { setApiWorkspace } from "@/lib/api/client";
import type { WorkspaceContext } from "@/lib/workspace";
import { shell } from "@/copy/shell";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Design pass 2 step 5a: the rail, the two switcher boxes and the breadcrumb are replaced by one top bar with five links, a
// scope control, search and a person menu. This file replaces tests/app-shell.test.tsx (the same frame behaviours, restated
// on the new structure) and adds the new behaviour: active state, keyboard, the disclosure menus and the Menu button.
let pathname = "/w/demo/projects/all/runs";
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

const ctx = (over: Partial<WorkspaceContext> = {}): WorkspaceContext => ({
  mode: "session",
  user: { id: "usr_1", email: "owner@example.test", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "demo", name: "Demo team" },
  role: "OWNER",
  permissions: ["run.read"],
  ownPermissions: [],
  memberships: [
    { slug: "demo", name: "Demo team", role: "OWNER" },
    { slug: "other", name: "Other team", role: "VIEWER" },
  ],
  projects: [{ id: "prj_1", slug: "web", name: "Web <b>app</b>" }],
  ...over,
});

function shellOf(value: WorkspaceContext | null, project = "all") {
  const page = (
    <AppShell base={{ ...BASE, project }}>
      <h1>Page body</h1>
    </AppShell>
  );
  return renderWithQuery(
    value ? <WorkspaceProvider value={value}>{page}</WorkspaceProvider> : page,
  );
}

const primary = () => within(screen.getByRole("navigation", { name: "Primary" }));

beforeEach(() => {
  pathname = "/w/demo/projects/all/runs";
  stubApi();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setApiWorkspace(null);
});

describe("application frame", () => {
  it("renders the skip link, one top bar and the page, with one Primary navigation and no rail or breadcrumb", () => {
    const { container } = shellOf(ctx());
    const frame = container.querySelector(".shell")!;
    expect([...frame.children].map((e) => e.tagName)).toEqual(["A", "HEADER", "MAIN"]);
    expect(screen.getAllByRole("navigation", { name: "Primary" })).toHaveLength(1);
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(container.querySelector("aside")).toBeNull();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("lists Runs, Review, Analytics, Experiments and (session only) Settings", () => {
    shellOf(ctx());
    expect(
      primary()
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Runs", "Review", "Analytics", "Experiments", "Settings"]);
    expect(primary().getByRole("link", { name: "Runs" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/runs",
    );
    // Step 5c: Settings lands on the first tab this role can read; a role with only run.read reads just Sessions.
    expect(primary().getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/w/demo/settings/sessions",
    );
  });

  it("marks the current page with aria-current, one link at a time", () => {
    const cases: [string, string][] = [
      ["/w/demo/projects/all/runs", "Runs"],
      ["/w/demo/projects/all/runs/run_1", "Runs"],
      ["/w/demo/projects/all/analytics", "Analytics"],
      ["/w/demo/projects/all/experiments/exp_1", "Experiments"],
      ["/w/demo/security", "Review"],
      ["/w/demo/approvals", "Review"],
      ["/w/demo/settings/quotas", "Settings"],
      ["/w/demo/settings/alerts", "Settings"],
    ];
    for (const [path, name] of cases) {
      pathname = path;
      const view = shellOf(ctx());
      const current = primary()
        .getAllByRole("link")
        .filter((a) => a.getAttribute("aria-current") === "page");
      expect(
        current.map((a) => a.textContent),
        path,
      ).toEqual([name]);
      view.unmount();
    }
  });

  it("Review leads to the approvals queue for a person who may read it, and to the security findings otherwise", () => {
    const first = shellOf(ctx({ permissions: ["run.read", "approval.read"] }));
    expect(primary().getByRole("link", { name: /^Review/ })).toHaveAttribute(
      "href",
      "/w/demo/approvals",
    );
    first.unmount();
    shellOf(ctx({ permissions: ["run.read"] }));
    expect(primary().getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      "/w/demo/security",
    );
  });

  it("Settings leads to the first tab the role may read, not always Members", () => {
    shellOf(ctx({ permissions: ["run.read", "pricing.read"] }));
    expect(primary().getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/w/demo/settings/sessions",
    );
  });

  it("hides Review from someone who can read neither runs nor approvals", () => {
    shellOf(ctx({ permissions: [] }));
    expect(primary().queryByRole("link", { name: "Review" })).toBeNull();
  });

  it("fixture mode: no Settings link, no email, no sign-out; the menu is just 'Account'", async () => {
    shellOf(ctx({ mode: "fixtures", user: null }));
    expect(primary().queryByRole("link", { name: "Settings" })).toBeNull();
    const account = screen.getByRole("button", { name: shell.person.account });
    await userEvent.setup().click(account);
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(screen.queryByText("owner@example.test")).toBeNull();
    // the panel says why it is bare instead of opening an empty box (the temporary links that filled it are gone, KI-279)
    expect(screen.getByText(shell.person.none)).toBeInTheDocument();
  });

  it("works without a workspace provider: brand, the links and the scope as plain text", () => {
    shellOf(null);
    expect(
      primary()
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Runs", "Review", "Analytics", "Experiments"]);
    expect(screen.getByText("demo / All projects")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Account|@/ })).toBeNull();
  });

  it("keyboard order is skip link, Menu, brand, the five links, scope, search, person, then the page", async () => {
    shellOf(ctx());
    const user = userEvent.setup();
    const seen: string[] = [];
    // jsdom loads no stylesheet, so the closed search field is tabbable here; in a browser it is display:none (e2e/top-bar.spec.ts).
    for (let i = 0; i < 12; i++) {
      await user.tab();
      seen.push(
        (
          document.activeElement?.getAttribute("aria-label") ??
          document.activeElement?.textContent ??
          ""
        ).trim(),
      );
    }
    expect(seen).toEqual([
      "Skip to content",
      "Menu",
      "Agent BlackBox",
      "Runs",
      "Review",
      "Analytics",
      "Experiments",
      "Settings",
      "Demo team / All projects",
      "Search runs",
      "Search all runs",
      "owner@example.test",
    ]);
  });
});

describe("the recording dot", () => {
  it("names itself Recording beside Runs while a run is running (fixtures have one)", async () => {
    shellOf(ctx());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Recording"));
    expect(screen.getByRole("status").querySelector(".live-dot")).not.toBeNull();
  });

  it("shows nothing when nothing is running", async () => {
    stubApi({
      "/v1/runs": () =>
        Response.json({ items: [], next_cursor: null, query: null }, { status: 200 }),
    });
    shellOf(ctx());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});

describe("the Menu button (below 900 px the links collapse into a panel)", () => {
  it("toggles the panel state, closes on Escape and gives focus back to the button", async () => {
    shellOf(ctx());
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Menu" });
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(nav).not.toHaveAttribute("data-open");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(nav).toHaveAttribute("data-open", "true");
    await user.tab(); // into the panel: the brand, then the links; none of it is hover-only
    await user.keyboard("{Escape}");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("closes when focus leaves the bar's start or the page is pressed", async () => {
    shellOf(ctx());
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "Menu" });
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("heading", { name: "Page body" }));
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("closes after a navigation (the open state belongs to one pathname)", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tree = () => (
      <QueryClientProvider client={client}>
        <WorkspaceProvider value={ctx()}>
          <AppShell base={BASE}>
            <h1>Page body</h1>
          </AppShell>
        </WorkspaceProvider>
      </QueryClientProvider>
    );
    const view = render(tree());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "true");
    pathname = "/w/demo/projects/all/analytics";
    view.rerender(tree());
    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });
});

describe("the scope control", () => {
  it("is one button reading 'workspace / project' that replaces the two boxes", () => {
    shellOf(ctx());
    const button = screen.getByRole("button", { name: "Demo team / All projects" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list", { name: "Projects" })).toBeNull();
  });

  it("lists workspaces and projects as links into Runs; names stay text", async () => {
    shellOf(ctx(), "web");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Demo team / Web <b>app</b>" }));
    const projects = screen.getByRole("list", { name: "Projects" });
    expect(within(projects).getByRole("link", { name: "All projects" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/runs",
    );
    const web = within(projects).getByRole("link", { name: "Web <b>app</b>" });
    expect(web).toHaveAttribute("href", "/w/demo/projects/web/runs");
    expect(web).toHaveAttribute("aria-current", "true");
    expect(projects.querySelector("b")).toBeNull();
    const workspaces = screen.getByRole("list", { name: "Your workspaces" });
    expect(within(workspaces).getByRole("link", { name: /^Other team/ })).toHaveAttribute(
      "href",
      "/w/other/projects/all/runs",
    );
  });

  it("offers no workspace list to someone with a single workspace", async () => {
    shellOf(ctx({ memberships: [{ slug: "demo", name: "Demo team", role: "OWNER" }] }));
    await userEvent.setup().click(screen.getByRole("button", { name: /Demo team/ }));
    expect(screen.queryByRole("list", { name: "Your workspaces" })).toBeNull();
    expect(screen.getByRole("list", { name: "Projects" })).toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the button", async () => {
    shellOf(ctx());
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: /Demo team/ });
    await user.click(button);
    expect(screen.getByRole("list", { name: "Projects" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("list", { name: "Projects" })).toBeNull();
    expect(button).toHaveFocus();
  });

  it("closes when focus tabs out of it", async () => {
    shellOf(ctx());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Demo team/ }));
    const links = within(screen.getByRole("list", { name: "Projects" })).getAllByRole("link");
    links.at(-1)!.focus();
    await user.tab();
    expect(screen.queryByRole("list", { name: "Projects" })).toBeNull();
  });
});

describe("the person menu", () => {
  it("shows who is signed in, the role, Sessions, and a Sign out button", async () => {
    shellOf(ctx({ permissions: ["run.read", "alert.read"] }));
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "owner@example.test" }));
    expect(screen.getByText("Role: Owner")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute(
      "href",
      "/w/demo/settings/sessions",
    );
    // The temporary entries of step 5a are gone (KI-279): Security findings is a tab of Review, Alerts a tab of Settings.
    expect(screen.queryByRole("link", { name: "Alerts" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Security findings" })).toBeNull();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("has no Alerts entry whatever the role, and closes on Escape with focus back", async () => {
    shellOf(ctx({ permissions: ["run.read", "alert.read"] }));
    const user = userEvent.setup();
    const button = screen.getByRole("button", { name: "owner@example.test" });
    await user.click(button);
    expect(screen.queryByRole("link", { name: "Alerts" })).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(button).toHaveFocus();
  });
});
