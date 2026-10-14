import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Link from "next/link";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PoliciesSettings } from "@/components/PoliciesSettings";
import { SettingsPage } from "@/components/SettingsPage";
import { resetPolicyFixtures } from "@/fixtures/policies";
import { STARTER_POLICY } from "@/lib/policy";
import { PRJ, errorBody, mount, stubPolicyApi } from "./policyHelpers";

const url = vi.hoisted(() => ({ search: "", subs: new Set<() => void>() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/settings/policies",
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
  resetPolicyFixtures();
  url.search = "project=demo";
  window.history.pushState = (_d, _t, u) => {
    realPush(_d, _t, u);
    url.search = new URL(String(u), "http://x").search.slice(1);
    url.subs.forEach((n) => n());
  };
});
afterEach(() => {
  window.history.pushState = realPush;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const editor = async () =>
  (await screen.findByRole("textbox", {
    name: /^Policy text( \(read only\))?$/,
  })) as HTMLTextAreaElement;
const edit = (box: HTMLTextAreaElement, value: string) =>
  fireEvent.change(box, { target: { value } });
const GOOD = `${STARTER_POLICY}\n  - id: deny-prod-shell\n    when:\n      capability: shell.execute\n      resource: terraform\n    decision: deny\n`;

describe("the Policies settings page", () => {
  it("states the cooperative guarantee in plain words, first", async () => {
    stubPolicyApi();
    mount(<PoliciesSettings />);
    const note = await screen.findByTestId("cooperative-note");
    expect(note).toHaveTextContent(
      "Policies are enforced for agents that ask Agent Black Box through the SDK guard. They do not stop code that bypasses it.",
    );
    expect(document.body.textContent).not.toMatch(/protected/i);
  });

  it("shows a project's effective policy with its version, source and metadata; the gutter numbers every line", async () => {
    stubPolicyApi();
    mount(<PoliciesSettings />);
    const box = await editor();
    expect(box.value).toBe(STARTER_POLICY);
    const meta = screen.getByTestId("policy-meta");
    expect(meta).toHaveTextContent("version 3");
    expect(meta).toHaveTextContent("this project's own policy");
    expect(meta).toHaveTextContent("3 rules");
    expect(meta).toHaveTextContent("Add the catastrophic-command rule.");
    const lines = STARTER_POLICY.split("\n").length;
    expect(screen.getByTestId("gutter").children).toHaveLength(lines);
    expect(box).toHaveAttribute("spellcheck", "false");
  });

  it("the workspace scope is the default, and the picker writes the project to the URL", async () => {
    url.search = "";
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await editor();
    expect(screen.getByTestId("policy-meta")).toHaveTextContent("the workspace policy");
    await user.selectOptions(screen.getByLabelText(/^Scope/), "demo");
    expect(url.search).toBe("project=demo");
    await waitFor(() => expect(screen.getByTestId("policy-meta")).toHaveTextContent("version 3"));
  });

  it("a project with no policy of its own shows the workspace text and says publishing replaces it", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/policies/current" && c.search.includes("project_id")
        ? Response.json({
            policy: {
              comment: null,
              created_at: "2026-10-04T10:00:00Z",
              created_by: "usr_1",
              document: "version: 1\npolicies: []\n",
              document_sha256: "x",
              project_id: null,
              rule_count: 0,
              scope: "workspace",
              version: 7,
            },
            project_id: PRJ,
            source: "workspace",
          })
        : undefined,
    );
    mount(<PoliciesSettings />);
    await editor();
    expect(screen.getByTestId("policy-meta")).toHaveTextContent(
      "inherited from the workspace policy",
    );
    expect(screen.getByTestId("inherited-note")).toHaveTextContent("replaces the workspace one");
  });

  it("checks the draft with the API as you type: issues with line:column, Publish stays off, a click moves the caret", async () => {
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, "version: 2\npolicies:\n  - id: Bad Id\n    decision: maybe\n");
    const list = await screen.findByTestId("issues", {}, { timeout: 3000 });
    const buttons = within(list).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([
      "1:10Only 'version: 1' is supported.",
      "3:9A rule id is a lowercase slug such as deny-env-read.",
      "4:15'decision' is one of: allow, deny, require_approval.",
    ]);
    expect(screen.getByTestId("check-status")).toHaveTextContent(
      "3 problems to fix before publishing.",
    );
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    // the gutter marks the lines with a problem
    const marked = [...screen.getByTestId("gutter").children].filter((c) =>
      c.hasAttribute("data-issue"),
    );
    expect(marked.map((c) => c.textContent)).toEqual(["1", "3", "4"]);
    await user.click(buttons[2]!);
    expect(box).toHaveFocus();
    const line4 = "version: 2\npolicies:\n  - id: Bad Id\n".length;
    expect(box.selectionStart).toBe(line4 + 14);
    const sent = calls.filter((c) => c.path === "v1/policies/validate");
    expect(sent).toHaveLength(1); // debounced: one request for the settled text
    expect((sent[0]!.body as { document: string }).document).toContain("Bad Id");
  });

  it("a valid draft says so and enables Publish; reverting to the published text sends nothing and turns it off", async () => {
    stubPolicyApi();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, GOOD);
    await waitFor(
      () => expect(screen.getByTestId("check-status")).toHaveTextContent("Valid: 4 rules."),
      {
        timeout: 3000,
      },
    );
    expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled();
    edit(box, STARTER_POLICY);
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByTestId("check-status")).toHaveTextContent(
      "Unchanged from the published version.",
    );
  });

  it("'Start from the example' puts the starter policy in the editor (after a confirm if the text would be lost)", async () => {
    url.search = "";
    stubPolicyApi();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    expect(box.value).not.toBe(STARTER_POLICY);
    await user.click(screen.getByRole("button", { name: "Start from the example" }));
    expect(confirm).toHaveBeenCalledWith("Replace the text in the editor with the example?");
    expect(box.value).toBe(STARTER_POLICY);
    expect(box.value).toMatch(
      /deny-env-read[\s\S]*approve-push-main[\s\S]*deny-catastrophic-shell/,
    );
    expect(box.value).toContain('resource: "**/.env*"');
    expect(box.value).toContain("expires_in: 15m");
    expect(box.value).toContain("risk_class: R4");
  });

  it("declining the replace keeps the text", async () => {
    url.search = "";
    stubPolicyApi();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    const before = box.value;
    await user.click(screen.getByRole("button", { name: "Start from the example" }));
    expect(box.value).toBe(before);
  });

  it("an empty scope offers the example", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/policies/current"
        ? Response.json({ policy: null, project_id: PRJ, source: "default" })
        : undefined,
    );
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    expect(await screen.findByText("No policy yet")).toBeInTheDocument();
    expect(screen.getByTestId("policy-meta")).toHaveTextContent("everything is allowed");
    await user.click(screen.getAllByRole("button", { name: "Start from the example" })[0]!);
    expect((await editor()).value).toBe(STARTER_POLICY);
  });

  it("dry run: posts the draft for the project and shows counts, rules and samples with run and event links", async () => {
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await editor();
    await user.click(screen.getByRole("button", { name: "Dry run" }));
    const panel = await screen.findByTestId("dry-run-result");
    const post = calls.find((c) => c.path === "v1/policies/dry-run")!;
    expect(post.body).toMatchObject({ project_id: PRJ, document: STARTER_POLICY });
    expect(panel).toHaveTextContent("Nothing was changed.");
    expect(within(panel).getAllByText("Would deny").length).toBeGreaterThan(0);
    const rules = within(panel).getByRole("table", { name: "By rule" });
    expect(within(rules).getByText("deny-env-read")).toBeInTheDocument();
    expect(within(rules).getByText("no rule (policy default)")).toBeInTheDocument();
    const samples = within(panel).getAllByTestId("dry-sample");
    expect(samples.length).toBeGreaterThan(0);
    const links = within(samples[0]!).getAllByRole("link");
    expect(links[0]!.getAttribute("href")).toMatch(/^\/w\/demo\/projects\/demo\/runs\/run_\w+$/);
    expect(links[1]!.getAttribute("href")).toMatch(/\/runs\/run_\w+\?event=evt_\w+$/);
    expect(panel.textContent).not.toMatch(/\.env|rm -rf/);
  });

  it("dry run says when it stopped early", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/policies/dry-run"
        ? Response.json({
            by_decision: { allow: 5 },
            by_rule: [{ count: 5, decision: "allow", rule_id: null }],
            events_evaluated: 5,
            events_skipped: 0,
            limits: { max_events: 20000, max_runs: 100, max_samples: 25, time_budget_seconds: 5 },
            runs_considered: 1,
            samples: [],
            truncated: "time_budget",
          })
        : undefined,
    );
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await editor();
    await user.click(screen.getByRole("button", { name: "Dry run" }));
    expect(await screen.findByTestId("dry-truncated")).toHaveTextContent(
      "It stopped at its time limit",
    );
    expect(screen.getByText("No action would be denied or need approval.")).toBeInTheDocument();
  });

  it("publish sends the draft, the comment and the base version, then shows the new version", async () => {
    const calls = stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, GOOD);
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled(), {
      timeout: 3000,
    });
    await user.type(screen.getByLabelText("Comment (optional)"), "Add a terraform rule.");
    await user.click(screen.getByRole("button", { name: "Publish" }));
    expect(await screen.findByTestId("published")).toHaveTextContent("Published version 4.");
    const post = calls.find((c) => c.path === "v1/policies/publish")!;
    expect(post.body).toEqual({
      document: GOOD,
      project_id: PRJ,
      base_version: 3,
      comment: "Add a terraform rule.",
    });
    await waitFor(() => expect(screen.getByTestId("policy-meta")).toHaveTextContent("version 4"));
    expect((await editor()).value).toBe(GOOD);
    const rows = await screen.findAllByTestId("version-row");
    expect(rows[0]).toHaveTextContent("v4");
    expect(rows[0]).toHaveTextContent("(current)");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("a concurrent publish is a conflict: 'Someone published a newer version', and Reload fetches it", async () => {
    let conflict = true;
    const calls = stubPolicyApi((c) =>
      c.path === "v1/policies/publish" && conflict
        ? errorBody("POLICY_VERSION_CONFLICT", 409, { current_version: 4 })
        : undefined,
    );
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, GOOD);
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled(), {
      timeout: 3000,
    });
    await user.click(screen.getByRole("button", { name: "Publish" }));
    const alert = await screen.findByTestId("conflict");
    expect(alert).toHaveTextContent("Someone published a newer version (version 4).");
    expect(alert.textContent).not.toContain("API sentence");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    conflict = false;
    const readsBefore = calls.filter((c) => c.path === "v1/policies/current").length;
    await user.click(screen.getByRole("button", { name: "Reload the latest version" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "v1/policies/current").length).toBeGreaterThan(
        readsBefore,
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("conflict")).toBeNull());
  });

  it("a 422 on publish lists the API's issues in the editor", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/policies/publish"
        ? errorBody("POLICY_INVALID", 422, {
            issues: [{ line: 2, column: 3, code: "unknown_key", message: "Unknown key 'x'." }],
          })
        : undefined,
    );
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, GOOD);
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish" })).toBeEnabled(), {
      timeout: 3000,
    });
    await user.click(screen.getByRole("button", { name: "Publish" }));
    const list = await screen.findByTestId("issues");
    expect(list).toHaveTextContent("2:3Unknown key 'x'.");
  });

  it("a document the web proxy would refuse says so and cannot be published", async () => {
    stubPolicyApi();
    mount(<PoliciesSettings />);
    const box = await editor();
    edit(box, `${GOOD}# ${"x".repeat(70_000)}\n`);
    expect(screen.getByTestId("check-status")).toHaveTextContent(
      "too large to publish from the web editor",
    );
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });
});

describe("history and diff", () => {
  it("lists versions newest first and shows a selected one read-only", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    const rows = await screen.findAllByTestId("version-row");
    expect(rows.map((r) => r.querySelector("td")?.textContent)).toEqual([
      "v3 (current)",
      "v2",
      "v1",
    ]);
    await user.click(screen.getByRole("button", { name: "View version 1" }));
    const view = await screen.findByTestId("version-view");
    const box = (await within(view).findByRole("textbox", {
      name: /Policy text of version 1/,
    })) as HTMLTextAreaElement;
    expect(box).toHaveAttribute("readonly");
    expect(box.value).toContain("deny-env-read");
    expect(box.value).not.toContain("approve-push-main");
    await user.click(screen.getByRole("button", { name: "Hide version 1" }));
    expect(screen.queryByTestId("version-view")).toBeNull();
  });

  it("'Compare with current' shows the unified diff as text: added lines bold ink, removed lines amber, no red", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await user.click(await screen.findByRole("button", { name: "View version 2" }));
    await user.click(await screen.findByRole("button", { name: "Compare with current (v3)" }));
    const diff = await screen.findByTestId("policy-diff");
    expect(diff).toHaveAccessibleName("Changes from version 2 to version 3");
    const spans = [...diff.querySelectorAll("span")];
    expect(spans.some((s) => s.dataset.kind === "add")).toBe(true);
    expect(spans.some((s) => s.dataset.kind === "del")).toBe(true);
    expect(spans.find((s) => s.dataset.kind === "add")!.className).toBe("pol-add");
    expect(spans.find((s) => s.dataset.kind === "del")!.className).toBe("pol-del");
    expect(diff.textContent).toContain("+  - id: deny-catastrophic-shell");
    expect(diff.innerHTML).not.toMatch(/danger|fail|bad|red/i);
    expect(screen.getByText(/Added lines are bold, removed lines are amber/)).toBeInTheDocument();
  });

  it("a truncated diff says so", async () => {
    stubPolicyApi((c) =>
      c.path === "v1/policies/diff"
        ? Response.json({
            diff: "--- v1\n+++ v3\n@@ -1 +1 @@\n-a\n+b\n",
            from_version: 1,
            to_version: 3,
            project_id: PRJ,
            truncated: true,
          })
        : undefined,
    );
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await user.click(await screen.findByRole("button", { name: "View version 1" }));
    await user.click(await screen.findByRole("button", { name: /^Compare with current/ }));
    expect(await screen.findByTestId("diff-truncated")).toHaveTextContent("cut short");
  });

  it("'Use as draft' loads an old version into the editor (editors only)", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<PoliciesSettings />);
    await user.click(await screen.findByRole("button", { name: "View version 1" }));
    await user.click(await screen.findByRole("button", { name: "Use as draft" }));
    const box = await editor();
    expect(box.value).toContain("deny-env-read");
    expect(box.value).not.toContain("approve-push-main");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled(); // until the API has checked it
  });
});

describe("read-only and role gating", () => {
  it("policy.read without policy.write: the text is read-only, with a note and no editing controls", async () => {
    const calls = stubPolicyApi();
    mount(<PoliciesSettings />, ["run.read", "policy.read"]);
    const box = await editor();
    expect(box).toHaveAttribute("readonly");
    expect(box).toHaveAccessibleName("Policy text (read only)");
    expect(screen.getByTestId("readonly-note")).toHaveTextContent(
      "You can read policies but not change them",
    );
    for (const name of ["Publish", "Dry run", "Start from the example"])
      expect(screen.queryByRole("button", { name })).toBeNull();
    edit(box, "version: 1\n");
    expect(box.value).toBe(STARTER_POLICY);
    await act(async () => {});
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    // history stays readable, and offers no way to put a version in the editor
    await userEvent.setup().click(await screen.findByRole("button", { name: "View version 1" }));
    expect(screen.queryByRole("button", { name: "Use as draft" })).toBeNull();
  });

  it("a role without policy.read gets 'Not available' from the settings page", () => {
    stubPolicyApi();
    mount(
      <SettingsPage needs="policy.read" what="policies">
        <PoliciesSettings />
      </SettingsPage>,
      ["run.read", "approval.read"],
    );
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
  });

  it("the API's 403 shows 'Not available' too", async () => {
    stubPolicyApi(() => errorBody("PERMISSION_DENIED", 403));
    mount(<PoliciesSettings />);
    expect(await screen.findByText("Not available for your role")).toBeInTheDocument();
  });

  it("mentions the audit log, with a link for roles that may read it", async () => {
    stubPolicyApi();
    mount(<PoliciesSettings />);
    const link = await screen.findByRole("link", { name: "audit log" });
    expect(link).toHaveAttribute("href", "/w/demo/settings/audit");
    expect(document.body.textContent).toMatch(/Publishing is written to the audit log/);
  });
});

describe("unsaved changes", () => {
  const withLink = () => (
    <>
      <PoliciesSettings />
      <Link href="/w/demo/approvals">Approvals</Link>
      <a href="#main">Skip</a>
    </>
  );

  it("asks before a reload when the draft differs, and not when it is clean", async () => {
    stubPolicyApi();
    mount(withLink());
    const box = await editor();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    edit(box, GOOD);
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
    edit(box, STARTER_POLICY);
    const again = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(again);
    expect(again.defaultPrevented).toBe(false);
  });

  it("asks before following an in-app link; declining keeps the page, accepting lets it go", async () => {
    stubPolicyApi();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mount(withLink());
    const box = await editor();
    edit(box, GOOD);
    const link = screen.getByRole("link", { name: "Approvals" });
    const stop = (e: Event) => e.preventDefault(); // jsdom does not navigate; this stands in for it
    document.addEventListener("click", stop);
    const declined = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(declined);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("unsaved changes"));
    expect(declined.defaultPrevented).toBe(true);
    confirm.mockReturnValue(true);
    let reached = false;
    const probe = () => (reached = true);
    document.addEventListener("click", probe); // bubble phase: reached only if the guard did not stop it
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(reached).toBe(true);
    document.removeEventListener("click", stop);
    document.removeEventListener("click", probe);
  });

  it("an in-page anchor and a clean editor never prompt; changing scope asks first", async () => {
    stubPolicyApi();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    mount(withLink());
    const box = await editor();
    await user.click(screen.getByRole("link", { name: "Approvals" }).closest("a")!).catch(() => {});
    expect(confirm).not.toHaveBeenCalled();
    edit(box, GOOD);
    screen
      .getByRole("link", { name: "Skip" })
      .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(confirm).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText(/^Scope/), "");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(url.search).toBe("project=demo"); // declined: still here
  });
});
