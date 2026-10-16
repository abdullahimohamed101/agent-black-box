import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SecuritySettings } from "@/components/SecuritySettings";
import { SettingsNav } from "@/components/SettingsNav";
import { SettingsPage } from "@/components/SettingsPage";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { fixtureSecuritySettings, resetFixtureSecuritySettings } from "@/fixtures/security";
import { HOST_PATTERN_MESSAGE, normalizeHostPattern } from "@/lib/security";
import type { WorkspaceContext } from "@/lib/workspace";

vi.mock("next/navigation", () => ({ usePathname: () => "/w/acme/settings/security" }));

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
const mount = (permissions = ["project.write", "audit.read"]) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <SettingsPage needs="project.write" what="security settings">
          <SecuritySettings />
        </SettingsPage>
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
};
const calls: { method: string; body: unknown }[] = [];
function api(failPut?: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      const u = new URL(r.url);
      if (!u.pathname.endsWith("/security-settings"))
        return Response.json({ error: { code: "NOPE", message: "no" } }, { status: 404 });
      let body: unknown;
      if (r.method === "PUT") {
        body = await r.json();
        calls.push({ method: "PUT", body });
        if (failPut) return failPut.clone();
      }
      const reply = fixtureSecuritySettings(PRJ, body, true);
      return Response.json(reply.body, { status: reply.status });
    }),
  );
}
beforeEach(() => {
  calls.length = 0;
  resetFixtureSecuritySettings();
});
afterEach(() => vi.unstubAllGlobals());

describe("host patterns mirror the API", () => {
  it.each([
    "api.example.com",
    "*.example.com",
    "  API.Example.com. ",
    "10.0.0.1",
    // 253 characters, the longest DNS name (review P3-5), with and without the wildcard
    [`${"a".repeat(63)}`, `${"b".repeat(63)}`, `${"c".repeat(63)}`, `${"d".repeat(61)}`].join("."),
    `*.${[`${"a".repeat(63)}`, `${"b".repeat(63)}`, `${"c".repeat(63)}`, `${"d".repeat(61)}`].join(".")}`,
    "xn--bcher-kva.example",
  ])("accepts %s", (v) => {
    expect(normalizeHostPattern(v).ok).toBe(true);
  });
  it.each([
    "https://api.example.com",
    "api.example.com:443",
    "api.example.com/x",
    "",
    "a_b.com",
    "*",
    `${"a".repeat(101)}.com`,
    "bücher.example",
    "*.münchen.de",
    // 254 characters: four labels of at most 63 do not fit (RFC 1035 allows 253)
    [`${"a".repeat(63)}`, `${"b".repeat(63)}`, `${"c".repeat(63)}`, `${"d".repeat(62)}`].join("."),
  ])("refuses %s with the fixed message that does not echo it", (v) => {
    const r = normalizeHostPattern(v);
    expect(r).toEqual({ ok: false, message: HOST_PATTERN_MESSAGE });
    if (v && v !== "*") expect(HOST_PATTERN_MESSAGE).not.toContain(v);
  });
  it("tells a person to write an international name in punycode", () => {
    expect(HOST_PATTERN_MESSAGE).toContain("punycode");
    expect(HOST_PATTERN_MESSAGE).toContain("xn--");
  });
});

describe("Security settings", () => {
  it("shows the capture modes with plain explanations and the new-events-only note", async () => {
    api();
    mount();
    const modes = await screen.findByRole("group", { name: "Capture mode" });
    expect(within(modes).getByRole("radio", { name: /^Metadata only \(default\)/ })).toBeChecked();
    // Review P2-2: Metadata only keeps commands, paths, URLs, error messages and run metadata
    // (shown to people who may read payloads) and drops payloads, diffs and artifact bodies.
    expect(modes).toHaveTextContent(
      "Commands, file paths, URLs, error messages, run metadata, timings and counts are kept",
    );
    expect(modes).toHaveTextContent("Inline payloads, diffs and artifact bodies are not stored");
    expect(modes).not.toHaveTextContent("Timings, types and counts");
    expect(modes).toHaveTextContent("after the server removes secret-shaped values");
    expect(modes).toHaveTextContent("also no error messages or run metadata");
    expect(modes).toHaveTextContent("applies to new events only");
    expect(screen.getByRole("link", { name: "audit log" })).toHaveAttribute(
      "href",
      "/w/acme/settings/audit",
    );
  });

  it("saves only what changed, then shows what the API stored", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findByRole("group", { name: "Capture mode" });
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /^Full/ }));
    await user.click(screen.getByRole("checkbox", { name: "Report risky git" }));
    await user.click(save);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]!.body).toEqual({ capture_mode: "FULL", muted_detectors: ["sec.risky_git"] });
    const status = screen.getByTestId("save-status");
    await waitFor(() => expect(status).toHaveTextContent("Saved."));
    expect(status).toHaveTextContent(
      "Existing runs are re-analysed in the background; recent runs first.",
    );
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("radio", { name: /^Full/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Report risky git" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("adds and removes allowlist hosts, validating before anything is sent", async () => {
    api();
    const user = userEvent.setup();
    mount();
    await screen.findByRole("group", { name: "Allowed hosts" });
    const input = screen.getByLabelText("Add a host");
    await user.type(input, "https://evil.example{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(HOST_PATTERN_MESSAGE);
    expect(screen.getByRole("alert")).not.toHaveTextContent("evil.example");
    expect(calls).toHaveLength(0);
    await user.clear(input);
    await user.type(input, "*.Example.org{Enter}");
    const list = screen.getByRole("list", { name: "Allowed hosts" });
    expect(within(list).getByText("*.example.org")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove api.example.com" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]!.body).toEqual({ destination_allowlist: ["*.example.org"] });
  });

  it("explains that with no allowlist nothing is reported for destinations", async () => {
    api();
    mount();
    expect(
      await screen.findByText(/With no allowlist nothing is reported for destinations/),
    ).toBeInTheDocument();
  });

  it("shows the API's fixed 422 message without echoing the value", async () => {
    api(
      Response.json(
        {
          error: {
            code: "SETTINGS_INVALID",
            message: "at most 256 allowlist entries",
            request_id: "r",
            retryable: false,
            details: {},
          },
        },
        { status: 422 },
      ),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByRole("group", { name: "Capture mode" });
    await user.click(screen.getByRole("radio", { name: /^Disabled/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 256 allowlist entries");
    expect(screen.getByTestId("save-status")).toHaveTextContent("");
  });

  it("a 403 on save is a clean message, not a crash", async () => {
    api(
      Response.json(
        {
          error: {
            code: "PERMISSION_DENIED",
            message: "Not allowed",
            request_id: "r",
            retryable: false,
            details: {},
          },
        },
        { status: 403 },
      ),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByRole("group", { name: "Capture mode" });
    await user.click(screen.getByRole("radio", { name: /^Full/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your role does not allow this");
  });

  it("is not available to a role without project.write, and the tab is hidden from the sub-nav", async () => {
    api();
    const view = mount(["audit.read", "member.read"]);
    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Capture mode" })).toBeNull();
    view.unmount();
    render(
      <WorkspaceProvider value={ctx(["audit.read", "member.read"])}>
        <SettingsNav />
      </WorkspaceProvider>,
    );
    expect(screen.queryByRole("link", { name: "Capture and detectors" })).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("the sub-nav lists Capture and detectors (the Security tab, renamed in step 5c) for owners and admins", () => {
    render(
      <WorkspaceProvider value={ctx(["project.write", "audit.read"])}>
        <SettingsNav />
      </WorkspaceProvider>,
    );
    expect(screen.getByRole("link", { name: "Capture and detectors" })).toHaveAttribute(
      "href",
      "/w/acme/settings/security",
    );
  });
});
