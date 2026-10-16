import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsFrame } from "@/components/SettingsFrame";
import { ReadOnly, Section, TwoStep } from "@/components/SettingsKit";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { firstSettingsHref, settingsTabs } from "@/lib/settingsNav";
import type { WorkspaceContext } from "@/lib/workspace";

// Design pass 2 step 5c: THE ONE settings pattern. The title line is "Settings / <Tab>" (the h1 is the tab's name), the section
// list is grouped and permission-filtered, one button folds it below 900 px, and a destructive action is two steps in place.
let pathname = "/w/demo/settings/quotas";
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn() }),
}));
afterEach(() => vi.unstubAllGlobals());

const ALL = [
  "member.read",
  "api_key.read",
  "policy.read",
  "project.write",
  "project.read",
  "retention.read",
  "pricing.read",
  "alert.read",
  "audit.read",
];
const ctx = (permissions: string[]): WorkspaceContext => ({
  mode: "session",
  user: { id: "usr_1", email: "me@x.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "demo", name: "Demo" },
  role: "OWNER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [],
});
const mount = (permissions: string[], body: React.ReactNode = <p>the tab</p>) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SettingsFrame>{body}</SettingsFrame>
      </WorkspaceProvider>
    </QueryClientProvider>,
  );

describe("the settings frame", () => {
  it("is one title line, 'Settings' then the tab's name as the only h1", () => {
    pathname = "/w/demo/settings/quotas";
    mount(ALL);
    expect(screen.getByRole("heading", { level: 1, name: "Quotas" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to settings" })).toHaveAttribute(
      "href",
      "/w/demo/settings/members",
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("lists the nine tabs in five groups, the open one current", () => {
    mount(ALL.concat("x"));
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    const groups = within(nav)
      .getAllByRole("list")
      .map((l) => [
        l.getAttribute("aria-labelledby"),
        within(l)
          .getAllByRole("link")
          .map((a) => a.textContent),
      ]);
    expect(groups).toEqual([
      ["st-group-people", ["Members", "Sessions"]],
      ["st-group-access", ["API keys", "Policies", "Capture and detectors"]],
      ["st-group-limits", ["Quotas", "Retention", "Pricing"]],
      ["st-group-notify", ["Alerts"]],
      ["st-group-record", ["Audit log"]],
    ]);
    expect(within(nav).getByRole("link", { name: "Quotas" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("per role: a tab the permissions do not list is absent, and an empty group disappears", () => {
    mount(["pricing.read"]);
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Sessions", "Pricing"]);
    expect(within(nav).queryByText("Notify")).toBeNull();
    expect(within(nav).queryByText("Record")).toBeNull();
  });

  it("below 900 px one button names the open tab and opens the list; Escape closes it and returns focus", async () => {
    const user = userEvent.setup();
    mount(ALL);
    const toggle = screen.getByRole("button", { name: /^Section: Quotas/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("st-nav-list")).toHaveAttribute("data-open", "true");
    await user.keyboard("{Escape}");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("gives each table cell its column's name (the phone layout shows it before the value)", async () => {
    mount(
      ALL,
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>a</td>
            <td>b</td>
          </tr>
        </tbody>
      </table>,
    );
    await waitFor(() =>
      expect(screen.getAllByRole("cell").map((c) => c.getAttribute("data-label"))).toEqual([
        "Name",
        "Role",
      ]),
    );
  });
});

describe("settingsTabs and the Settings link", () => {
  it("lands on the first tab the role may read", () => {
    expect(firstSettingsHref("demo", ["member.read"])).toBe("/w/demo/settings/members");
    expect(firstSettingsHref("demo", ["api_key.read"])).toBe("/w/demo/settings/sessions");
    expect(firstSettingsHref("demo", [])).toBe("/w/demo/settings/sessions");
    expect(settingsTabs("demo", ["alert.read"]).map((t) => t.href)).toContain(
      "/w/demo/settings/alerts",
    );
  });
});

describe("the pieces of the pattern", () => {
  it("a Section is an h2 band with its sentence", () => {
    render(
      <Section title="Invitations" lede="One sentence.">
        <p>work</p>
      </Section>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Invitations" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Invitations" })).toBeInTheDocument();
    expect(screen.getByText("One sentence.")).toBeInTheDocument();
  });

  it("a read-only role gets one plain sentence, not a disabled form", () => {
    render(<ReadOnly />);
    expect(screen.getByRole("note")).toHaveTextContent(
      "Your role can read this but not change it. Owners and admins can.",
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  describe("TwoStep (destructive actions)", () => {
    it("acts only on the second press, and Cancel puts the first button back with focus", async () => {
      const user = userEvent.setup();
      const onConfirm = vi.fn();
      render(<TwoStep label="Remove" confirmLabel="Confirm remove" onConfirm={onConfirm} />);
      await user.click(screen.getByRole("button", { name: "Remove" }));
      expect(onConfirm).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Confirm remove" })).toHaveFocus();
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onConfirm).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Remove" })).toHaveFocus();
      await user.click(screen.getByRole("button", { name: "Remove" }));
      await user.click(screen.getByRole("button", { name: "Confirm remove" }));
      expect(onConfirm).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    });

    it("Escape undoes the confirm step", async () => {
      const user = userEvent.setup();
      const onConfirm = vi.fn();
      render(<TwoStep label="Revoke" confirmLabel="Confirm revoke" onConfirm={onConfirm} />);
      await user.click(screen.getByRole("button", { name: "Revoke" }));
      await user.keyboard("{Escape}");
      expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
      expect(onConfirm).not.toHaveBeenCalled();
    });
  });
});
