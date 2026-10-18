import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { resetPolicyFixtures } from "@/fixtures/policies";
import {
  DECISIONS_RUN_N,
  WAITING_APPROVAL_ID,
  WAITING_RUN_N,
  policyDecisionsRun,
  waitingForApprovalRun,
} from "@/fixtures/policyRuns";
import { approvalRun } from "@/fixtures/runs";
import { fid } from "@/fixtures/ids";
import { firstErrorFor, filterEvents, isError, eventClass, CLASS_LABELS } from "@/lib/timeline";
import { headlineModel } from "@/lib/runHeadline";
import { BASE } from "./helpers";
import { mount, stubPolicyApi } from "./policyHelpers";

const nav = vi.hoisted(() => ({ query: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/demo/runs/x",
  useSearchParams: () => new URLSearchParams(nav.query),
}));

beforeEach(() => resetPolicyFixtures());
afterEach(() => {
  vi.unstubAllGlobals();
  nav.query = "";
});

const rows = () => screen.getAllByRole("option");
const rowFor = (type: string, nth = 0) =>
  rows().filter((r) => r.querySelector(".tl-type")?.textContent === type)[nth]!;

describe("decisions on the run page", () => {
  it("labels every decision in the timeline with a glyph and a word, and the policy's own reason", async () => {
    stubPolicyApi();
    mount(<RunDetail runId={fid("run", DECISIONS_RUN_N)} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(/of \d+ events shown/),
    );
    const text = (type: string, nth = 0) => rowFor(type, nth).textContent ?? "";
    expect(text("policy.decision", 0)).toContain("Allowed (default)");
    expect(text("policy.action.blocked")).toContain("Denied by deny-env-read v3");
    expect(text("policy.action.blocked")).toContain(
      "Environment files hold secrets, so agents do not read them.",
    );
    expect(text("policy.decision", 1)).toContain("Approval required by approve-push-main v3");
    expect(text("approval.requested")).toContain("Approval requested");
    expect(text("approval.granted")).toContain("Approved");
    expect(text("approval.consumed")).toContain("Approval used");
    // glyphs are decoration; the words carry the meaning
    const blocked = rowFor("policy.action.blocked");
    expect(blocked.querySelector(".pol-glyph")).toHaveAttribute("aria-hidden", "true");
    expect(blocked.querySelector(".pol-glyph")?.textContent).toBe("⊘");
  });

  it("marks the events the policy service wrote, and only those", async () => {
    stubPolicyApi();
    mount(<RunDetail runId={fid("run", DECISIONS_RUN_N)} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(/of \d+ events shown/),
    );
    const service = rows().filter((r) => r.textContent?.includes("Recorded by the policy service"));
    expect(service.map((r) => r.querySelector(".tl-type")?.textContent)).toEqual([
      "policy.decision",
      "policy.action.blocked",
      "policy.decision",
      "approval.requested",
      "approval.granted",
      "approval.consumed",
    ]);
    expect(rowFor("file.read").textContent).not.toContain("policy service");
    expect(rowFor("git.push").textContent).not.toContain("policy service");
  });

  it("a denial is not a failure: no error mark, no fault line, no red, and the run stays a success", async () => {
    stubPolicyApi();
    const f = policyDecisionsRun();
    const blocked = f.events.find((e) => e.event_type === "policy.action.blocked")!;
    expect(isError(blocked)).toBe(false);
    expect(firstErrorFor(f.run.status, f.events)).toBeUndefined();
    mount(<RunDetail runId={f.run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(/of \d+ events shown/),
    );
    expect(document.querySelectorAll("[data-error], [data-first-error], .tl-fault")).toHaveLength(
      0,
    );
    expect(screen.getByTestId("headline")).toHaveTextContent(/^Completed in/);
    expect(screen.queryByTestId("first-error")).toBeNull();
    expect(rowFor("policy.action.blocked").querySelector(".tl-pol")).toHaveAttribute(
      "data-tone",
      "ink",
    );
  });

  it("the 'Approvals & policy' class holds exactly the decision events", () => {
    const f = policyDecisionsRun();
    const only = filterEvents(f.events, { classes: new Set(["approval"]), errorsOnly: false });
    expect(only.map((e) => e.event_type)).toEqual([
      "policy.decision",
      "policy.action.blocked",
      "policy.decision",
      "approval.requested",
      "approval.granted",
      "approval.consumed",
    ]);
    expect(CLASS_LABELS.approval).toBe("Approvals & policy");
    expect(eventClass("policy.warning")).toBe("approval");
  });

  it("the inspector explains a decision: rule, version, reason, approval, and who wrote it", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<RunDetail runId={fid("run", DECISIONS_RUN_N)} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(/of \d+ events shown/),
    );
    await user.click(rowFor("policy.action.blocked"));
    const drawer = await screen.findByTestId("drawer-policy");
    expect(drawer).toHaveTextContent("Denied by deny-env-read v3");
    expect(drawer).toHaveTextContent("Environment files hold secrets");
    expect(drawer).toHaveTextContent("deny-env-read");
    expect(drawer).toHaveTextContent("filesystem.read");
    expect(within(drawer).getByTestId("provenance")).toHaveTextContent(
      "Recorded by the policy service, not by the agent.",
    );
    expect(screen.getByRole("dialog")).not.toHaveAttribute("data-error");
  });

  it("an event from an agent that merely looks like a decision gets no provenance claim", async () => {
    stubPolicyApi();
    const user = userEvent.setup();
    mount(<RunDetail runId={fid("run", DECISIONS_RUN_N)} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(rowFor("file.read"));
    await screen.findByRole("dialog");
    expect(screen.queryByTestId("drawer-policy")).toBeNull();
  });

  it("?event= opens that event once the run has loaded (the link from a dry run sample)", async () => {
    stubPolicyApi();
    const f = policyDecisionsRun();
    const target = f.events.find((e) => e.event_type === "policy.action.blocked")!;
    nav.query = `event=${target.event_id}`;
    mount(<RunDetail runId={f.run.id} base={BASE} />);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("policy.action.blocked");
  });
});

describe("a run waiting for a person", () => {
  it("names the capability and the real time left, and links to Approvals for roles that can read them", async () => {
    const calls = stubPolicyApi();
    const f = waitingForApprovalRun();
    mount(<RunDetail runId={f.run.id} base={BASE} />);
    const headline = await screen.findByTestId("headline");
    await waitFor(() =>
      expect(headline).toHaveTextContent(/^Waiting for approval of github\.push at step \d+\.$/),
    );
    await waitFor(() =>
      expect(screen.getByTestId("headline-detail")).toHaveTextContent(
        /^\d+ h( \d+ min)? left · waiting /,
      ),
    );
    expect(calls.some((c) => c.path === `v1/approvals/${WAITING_APPROVAL_ID}`)).toBe(true);
    const banner = screen.getByTestId("approval-banner");
    expect(banner).toHaveTextContent("Waiting for a person to approve github.push");
    expect(banner).toHaveTextContent(/left/);
    expect(within(banner).getByRole("link", { name: "Open Approvals" })).toHaveAttribute(
      "href",
      `/w/demo/approvals?approval=${WAITING_APPROVAL_ID}`,
    );
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument(); // the status badge keeps its word
    expect(document.querySelector(".tl-fault")).toBeNull();
  });

  it("the time left counts down from the approval's own figure and never goes negative", async () => {
    stubPolicyApi((c) =>
      c.path === `v1/approvals/${WAITING_APPROVAL_ID}`
        ? Response.json({ ...approvalBody(), seconds_left: 3 })
        : undefined,
    );
    const f = waitingForApprovalRun();
    mount(<RunDetail runId={f.run.id} base={BASE} />);
    await waitFor(() =>
      expect(screen.getByTestId("headline-detail")).toHaveTextContent("3 s left"),
    );
    await waitFor(
      () => expect(screen.getByTestId("headline-detail")).toHaveTextContent("Expired"),
      { timeout: 6000 },
    );
    expect(screen.getByTestId("headline-detail").textContent).not.toMatch(/-\d/);
  });

  it("without approval.read the headline names the capability but states no time, reads no approval, and has no link", async () => {
    const calls = stubPolicyApi();
    const f = waitingForApprovalRun();
    mount(<RunDetail runId={f.run.id} base={BASE} />, ["run.read"]);
    const headline = await screen.findByTestId("headline");
    await waitFor(() =>
      expect(headline).toHaveTextContent(/^Waiting for approval of github\.push/),
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId("headline-detail").textContent).not.toMatch(/left/);
    expect(calls.some((c) => c.path.startsWith("v1/approvals"))).toBe(false);
    const banner = screen.getByTestId("approval-banner");
    expect(within(banner).queryByRole("link")).toBeNull();
    expect(banner).toHaveTextContent("Someone with approval rights decides this request.");
  });

  it("an older waiting run with no capability on its event keeps the plain words", async () => {
    stubPolicyApi();
    mount(<RunDetail runId={approvalRun().run.id} base={BASE} />);
    expect(await screen.findByTestId("headline")).toHaveTextContent(
      /^Waiting for approval at step \d+\.$/,
    );
  });

  it("the headline model is pure: capability from the event, time only when given", () => {
    const f = waitingForApprovalRun();
    const base = {
      run: f.run,
      events: f.events,
      t0: 0,
      eventsComplete: true,
      now: Date.parse(f.run.updated_at) + 5000,
    };
    expect(headlineModel(base).lead).toMatch(
      /^Waiting for approval of github\.push at step \d+\.$/,
    );
    expect(headlineModel(base).detail).not.toMatch(/left/);
    expect(headlineModel({ ...base, approvalLeftSeconds: 720 }).detail).toMatch(/^12 min left · /);
    expect(headlineModel({ ...base, approvalLeftSeconds: -5 }).detail).toMatch(/^Expired · /);
    expect(WAITING_RUN_N).toBe(20);
  });
});

function approvalBody() {
  return {
    agent_id: "release-agent",
    allow_self_approval: false,
    approver_role: "ADMIN",
    arguments: {},
    capability: "github.push",
    closed_at: null,
    comment: null,
    consumed: false,
    consumed_at: null,
    content_withheld: false,
    decided_at: null,
    decided_by: null,
    expires_at: "2099-01-01T00:00:00Z",
    id: WAITING_APPROVAL_ID,
    on_expiry: "deny",
    policy_id: "approve-push-main",
    policy_version: 3,
    project_id: "prj_00000000000000000000000001",
    redactions: 0,
    requested_at: "2026-10-13T11:00:00Z",
    resource: "origin main",
    risk_class: "R3",
    run_id: fid("run", WAITING_RUN_N),
    seconds_left: 100,
    state: "REQUESTED",
  };
}
