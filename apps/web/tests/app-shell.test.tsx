import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/AppShell";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { useRun } from "@/lib/queries";
import { setApiWorkspace } from "@/lib/api/client";
import type { WorkspaceContext } from "@/lib/workspace";
import { BASE, renderWithQuery, stubApi } from "./helpers";

let pathname = "/w/demo/projects/all";
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

function shell(value: WorkspaceContext | null, project = "all") {
  const page = (
    <AppShell base={{ ...BASE, project }}>
      <h1>Page body</h1>
    </AppShell>
  );
  return renderWithQuery(
    value ? <WorkspaceProvider value={value}>{page}</WorkspaceProvider> : page,
  );
}

beforeEach(() => {
  pathname = "/w/demo/projects/all";
  stubApi();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setApiWorkspace(null);
});

describe("application frame", () => {
  it("renders the rail, the top bar and the page in that order, with one Primary navigation", () => {
    const { container } = shell(ctx());
    const shellEl = container.querySelector(".shell")!;
    expect([...shellEl.children].map((e) => e.tagName)).toEqual(["A", "ASIDE", "HEADER", "MAIN"]);
    expect(screen.getAllByRole("navigation", { name: "Primary" })).toHaveLength(1);
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("lists Overview, Runs, Analytics, Experiments and (session only) Settings", () => {
    shell(ctx());
    const nav = within(screen.getByRole("navigation", { name: "Primary" }));
    expect(nav.getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Overview",
      "Runs",
      "Analytics",
      "Experiments",
      "Settings",
    ]);
    expect(nav.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("owner@example.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("fixture mode: no Settings link, no person, no sign-out", () => {
    shell(ctx({ mode: "fixtures", user: null }));
    const nav = within(screen.getByRole("navigation", { name: "Primary" }));
    expect(nav.queryByRole("link", { name: "Settings" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("works without a workspace provider: brand, primary links and crumbs only", () => {
    shell(null);
    const nav = within(screen.getByRole("navigation", { name: "Primary" }));
    expect(nav.getAllByRole("link")).toHaveLength(4);
    expect(screen.queryByRole("status")).toBeNull(); // no live indicator without a workspace
    expect(screen.getByText("All projects")).toBeInTheDocument();
  });

  it("switches workspace and project with links; project names stay text", () => {
    shell(ctx(), "web");
    const projects = screen.getByRole("list", { name: "Projects" });
    expect(within(projects).getByRole("link", { name: "All projects" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all",
    );
    const web = within(projects).getByRole("link", { name: "Web <b>app</b>" });
    expect(web).toHaveAttribute("href", "/w/demo/projects/web");
    expect(web).toHaveAttribute("aria-current", "true");
    expect(projects.querySelector("b")).toBeNull();
    expect(
      within(screen.getByRole("list", { name: "Your workspaces" })).getByRole("link", {
        name: "Other team",
      }),
    ).toHaveAttribute("href", "/w/other/projects/all");
  });

  it("breadcrumbs follow the path; a run shows its name as text", async () => {
    pathname = "/w/demo/projects/all/runs";
    const first = shell(ctx());
    expect(
      within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("Runs"),
    ).toHaveAttribute("aria-current", "page");
    first.unmount();
    pathname = `/w/demo/projects/all/runs/run_${"2".padStart(26, "0")}`;
    // The crumb only reads the cache the run page fills (see crumbs-hydration.test.tsx), so the page is stood in for.
    function RunPage() {
      useRun(`run_${"2".padStart(26, "0")}`);
      return null;
    }
    renderWithQuery(
      <WorkspaceProvider value={ctx()}>
        <AppShell base={{ ...BASE, project: "all" }}>
          <RunPage />
        </AppShell>
      </WorkspaceProvider>,
    );
    const crumbs = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
    expect(crumbs.getByRole("link", { name: "Runs" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/runs",
    );
    await waitFor(() => expect(crumbs.getByText("Migrate billing schema")).toBeInTheDocument());
  });

  it("the live indicator says Recording when a run is running (fixtures have one)", async () => {
    shell(ctx());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Recording"));
  });

  it("the live indicator says Idle when nothing is running", async () => {
    stubApi({
      "/v1/runs": () =>
        Response.json({ items: [], next_cursor: null, query: null }, { status: 200 }),
    });
    shell(ctx());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Idle"));
  });
});
