import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalDetailPanel } from "@/components/ApprovalDetailPanel";
import { fixtureReply } from "@/fixtures/api";
import { resetPolicyFixtures } from "@/fixtures/policies";
import { apr, errorBody, mount, stubPolicyApi } from "./policyHelpers";

// Review P2-3 and P2-4: the approver sees the exact command; content the scanner withheld is
// described truthfully (not "hidden by your role") and cannot be approved, only denied.

beforeEach(() => resetPolicyFixtures());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const panel = (id: string) =>
  mount(
    <ApprovalDetailPanel
      id={id}
      canDecide
      now={Date.now()}
      runHref={() => "/runs/x"}
      projectName={() => "Demo project"}
      onClose={() => undefined}
    />,
  );

const withheld = (id: string) => (c: { method: string; path: string }) => {
  if (c.method !== "GET" || c.path !== `v1/approvals/${id}`) return undefined;
  const base = fixtureReply(["v1", "approvals", id], new URLSearchParams()).body as object;
  return Response.json({ ...base, content_withheld: true, arguments: null, resource: null });
};

describe("the approval detail", () => {
  it("shows the exact shell command the approval covers", async () => {
    stubPolicyApi();
    panel(apr(2)); // the kubectl request
    const command = await screen.findByTestId("approval-command");
    expect(command).toHaveTextContent("kubectl apply -f deploy/staging.yaml");
    expect(screen.getByText(/Approving covers exactly this command, once/)).toBeInTheDocument();
  });

  it("says why the content is missing, never that the role hides it, and offers only Deny", async () => {
    stubPolicyApi(withheld(apr(1)));
    const user = userEvent.setup();
    panel(apr(1));
    const note = await screen.findByTestId("content-unavailable");
    expect(note).toHaveTextContent(/could not be shown/);
    expect(note).toHaveTextContent(/cannot be approved/);
    expect(document.body.textContent).not.toMatch(/hidden by your role/i);
    const form = screen.getByRole("form", { name: "Decide this request" });
    expect(within(form).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(form).getByRole("button", { name: "Deny" })).toBeEnabled();
    await user.click(within(form).getByRole("button", { name: "Deny" }));
    expect(within(form).getByRole("button", { name: "Confirm deny" })).toBeEnabled();
  });

  it("explains the server's refusal to approve withheld content", async () => {
    stubPolicyApi((c) => {
      if (c.method === "POST" && c.path.endsWith("/decision"))
        return errorBody("APPROVAL_CONTENT_UNAVAILABLE", 409);
      return undefined;
    });
    const user = userEvent.setup();
    panel(apr(3));
    const form = await screen.findByRole("form", { name: "Decide this request" });
    await user.click(within(form).getByRole("button", { name: "Approve" }));
    await user.click(within(form).getByRole("button", { name: "Confirm approve" }));
    await waitFor(() =>
      expect(screen.getByTestId("decision-error")).toHaveTextContent(
        "The content of this request could not be shown, so it cannot be approved. You can deny it.",
      ),
    );
  });
});
