import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewFrame } from "@/components/ReviewFrame";
import { mount, stubPolicyApi } from "./policyHelpers";

// Design pass 2 step 5c: Review is one page with two tabs (Approvals, Security findings); the routes keep their addresses and are
// the tabs' URLs. The frame owns the h1, the waiting count and the shared tab list; a tab the role may not read is absent.
let pathname = "/w/demo/approvals";
const push = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push }),
}));
beforeEach(() => {
  pathname = "/w/demo/approvals";
  push.mockClear();
  stubPolicyApi();
});
afterEach(() => vi.unstubAllGlobals());

const frame = () => (
  <ReviewFrame>
    <p>the page</p>
  </ReviewFrame>
);

describe("the Review frame", () => {
  it("has one h1 and the two tabs for a role that reads both, the open route's tab selected", async () => {
    mount(frame(), ["run.read", "approval.read"]);
    expect(screen.getByRole("heading", { level: 1, name: "Review" })).toBeInTheDocument();
    const list = screen.getByRole("tablist", { name: "Review sections" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Approvals", "Security findings"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[0]!.id);
    expect(screen.getByText("the page")).toBeInTheDocument();
  });

  it("selects Security findings on the security route, and keeps deep links such as ?approval= on Approvals", () => {
    pathname = "/w/demo/security";
    const first = mount(frame(), ["run.read", "approval.read"]);
    expect(screen.getByRole("tab", { name: "Security findings" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    first.unmount();
    pathname = "/w/demo/approvals"; // the search string is not part of the pathname: ?approval=apr_1 is still Approvals
    mount(frame(), ["run.read", "approval.read"]);
    expect(screen.getByRole("tab", { name: "Approvals" })).toHaveAttribute("aria-selected", "true");
  });

  it("says how many requests wait, in words, beside the title", async () => {
    mount(frame(), ["run.read", "approval.read"]);
    expect(await screen.findByText("6 waiting")).toBeInTheDocument();
  });

  it("a role that cannot read approvals sees only Security findings and no count", async () => {
    pathname = "/w/demo/security";
    mount(frame(), ["run.read"]);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Security findings"]);
    await waitFor(() => expect(screen.queryByText(/waiting/)).toBeNull());
  });

  it("a role that can read neither gets no tab list, but still the title and the page's own explanation", () => {
    mount(frame(), []);
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(document.querySelector("[aria-labelledby]")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Review" })).toBeInTheDocument();
  });

  it("a tab list whose open route is not readable is still reachable by keyboard (one tab stop)", () => {
    pathname = "/w/demo/approvals";
    mount(frame(), ["run.read"]); // may not read approvals, but is on that route
    const tab = screen.getByRole("tab", { name: "Security findings" });
    expect(tab).toHaveAttribute("tabindex", "0");
  });

  it("arrow keys, Home and End move between the tabs and go to the route", async () => {
    const user = userEvent.setup();
    mount(frame(), ["run.read", "approval.read"]);
    screen.getByRole("tab", { name: "Approvals" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(push).toHaveBeenLastCalledWith("/w/demo/security");
    await user.keyboard("{Home}");
    expect(push).toHaveBeenLastCalledWith("/w/demo/approvals");
    await user.keyboard("{End}");
    expect(push).toHaveBeenLastCalledWith("/w/demo/security");
  });

  it("a click on the other tab goes to its route", async () => {
    const user = userEvent.setup();
    mount(frame(), ["run.read", "approval.read"]);
    await user.click(screen.getByRole("tab", { name: "Security findings" }));
    expect(push).toHaveBeenCalledWith("/w/demo/security");
  });
});
