import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionsSettings } from "@/components/SessionsSettings";
import { SettingsNav } from "@/components/SettingsNav";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { fixtureSessions, resetFixtureSessions } from "@/fixtures/sessions";
import type { WorkspaceContext } from "@/lib/workspace";

vi.mock("next/navigation", () => ({ usePathname: () => "/w/acme/settings/sessions" }));

const ctx = (permissions: string[]): WorkspaceContext => ({
  mode: "session",
  user: { id: "u", email: "me@x.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "acme", name: "Acme" },
  role: "VIEWER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [],
});
const mount = (permissions: string[] = ["run.read"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SessionsSettings />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
};
const calls: string[] = [];
function api(options: { status?: number; failWrites?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      const u = new URL(r.url);
      const path = u.pathname.replace(/^\/api\/abb/, "");
      calls.push(`${r.method} ${path}`);
      if (options.status)
        return Response.json(
          { error: { code: "X", message: "It broke.", category: "INTERNAL", request_id: "req_9" } },
          { status: options.status },
        );
      if (options.failWrites && r.method !== "GET")
        return Response.json(
          { error: { code: "RATE_LIMITED", message: "Slow down.", category: "RATE_LIMIT" } },
          { status: 429 },
        );
      const reply = fixtureSessions(r.method, path.split("/").filter(Boolean));
      if (!reply) return Response.json({ error: { code: "NOPE", message: "no" } }, { status: 404 });
      return reply.status === 204
        ? new Response(null, { status: 204 })
        : Response.json(reply.body, { status: reply.status });
    }),
  );
}
beforeEach(() => {
  calls.length = 0;
  resetFixtureSessions();
});
afterEach(() => vi.unstubAllGlobals());

describe("the list", () => {
  it("shows each session with its device, times and a word for the current one", async () => {
    api();
    mount();
    const items = await screen.findAllByTestId("session-item");
    expect(items).toHaveLength(3);
    expect(within(items[0]!).getByText("Firefox on Linux")).toBeInTheDocument();
    expect(within(items[0]!).getByText("This session")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Safari on iOS")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Signed in")).toBeInTheDocument();
    expect(within(items[2]!).getByText("Unknown device")).toBeInTheDocument();
    for (const item of items) {
      expect(item.querySelector(".session-glyph")).toHaveAttribute("aria-hidden", "true");
      expect(within(item).getByText("Last used")).toBeInTheDocument();
    }
  });

  it("gives the current session no revoke control, only a pointer to Sign out", async () => {
    api();
    mount();
    const [current, other] = await screen.findAllByTestId("session-item");
    expect(within(current!).queryByRole("button")).toBeNull();
    expect(within(current!).getByText(/use Sign out/)).toBeInTheDocument();
    expect(
      within(other!).getByRole("button", { name: /End session on Safari on iOS/ }),
    ).toBeInTheDocument();
  });

  it("is open to every role: no permission is needed, and the tab shows for a viewer", async () => {
    api();
    mount([]);
    expect(await screen.findAllByTestId("session-item")).toHaveLength(3);
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <WorkspaceProvider value={ctx(["run.read"])}>
          <SettingsNav />
        </WorkspaceProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute(
      "href",
      "/w/acme/settings/sessions",
    );
    expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "API keys" })).toBeNull();
  });

  it("shows a plain loading state, then an error with a retry", async () => {
    api({ status: 500 });
    mount();
    expect(screen.getByText(/Loading sessions/)).toBeInTheDocument();
    expect(await screen.findByText("Could not load this data")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says so when there is nothing to list", async () => {
    api();
    vi.mocked(fetch).mockImplementationOnce(async () => Response.json({ items: [] }));
    mount();
    expect(await screen.findByText("No sessions")).toBeInTheDocument();
  });
});

describe("ending sessions", () => {
  it("ends one session and says so", async () => {
    api();
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /End session on Safari on iOS/ }));
    await waitFor(() => expect(screen.getAllByTestId("session-item")).toHaveLength(2));
    expect(screen.getByTestId("session-status")).toHaveTextContent("Session ended.");
    expect(calls).toContain("DELETE /v1/auth/sessions/00000000-0000-4000-8000-000000000002");
  });

  it("asks before signing out everywhere else, and Cancel does nothing", async () => {
    api();
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));
    expect(screen.getByText(/Sign out 2 other sessions\?/)).toBeInTheDocument();
    expect(screen.getByText(/This device stays signed in/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Sign out everywhere else" })).toBeInTheDocument();
    expect(calls.filter((c) => c.startsWith("POST"))).toEqual([]);
  });

  it("signs out everywhere else after the confirmation and keeps the current session", async () => {
    api();
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));
    await user.click(screen.getByRole("button", { name: "Confirm sign out everywhere else" }));
    await waitFor(() => expect(screen.getAllByTestId("session-item")).toHaveLength(1));
    expect(screen.getByTestId("session-status")).toHaveTextContent("Signed out 2 other sessions.");
    expect(screen.getByText("This session")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sign out everywhere else/ })).toBeNull();
    expect(calls).toContain("POST /v1/auth/sessions/revoke-others");
  });

  it("offers no sign-out-everywhere when this is the only session", async () => {
    api();
    vi.mocked(fetch).mockImplementationOnce(async () =>
      Response.json({
        items: [
          fixtureSessions("GET", ["v1", "auth", "sessions"]) && {
            ...(fixtureSessions("GET", ["v1", "auth", "sessions"])!.body as { items: object[] })
              .items[0]!,
          },
        ],
      }),
    );
    mount();
    await screen.findAllByTestId("session-item");
    expect(screen.queryByRole("button", { name: /everywhere else/ })).toBeNull();
  });

  it("shows a refused write as a clean banner and leaves the list alone", async () => {
    api({ failWrites: true });
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /End session on Safari on iOS/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Slow down.");
    expect(screen.getAllByTestId("session-item")).toHaveLength(3);
  });
});
