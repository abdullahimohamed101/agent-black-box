import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { withFindings } from "@/fixtures/findings";
import { failingChainRun, loopingRun, successRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(""),
}));
afterEach(() => vi.unstubAllGlobals());

// Phase 22 D3 put findings in a context column behind a "Context" button; design pass 2 (ADR-166) makes them the Findings tab.
describe("findings in the Findings tab (design pass 2)", () => {
  it("the tab counts the findings; each finding is one line with the way to its evidence", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = withFindings(failingChainRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const tab = screen.getByRole("tab", { name: "Findings" });
    expect(await within(tab).findByText("2")).toBeInTheDocument(); // the count is decoration beside the name
    expect(tab).toHaveAttribute("aria-selected", "false");
    await user.click(tab);
    const lines = await screen.findAllByTestId("finding");
    expect(lines).toHaveLength(2);
    for (const li of lines) {
      const head = li.querySelector(".finding-head")!;
      // severity (glyph and word), the title and the evidence button share the head line; the rest is behind Details
      expect(within(head as HTMLElement).getByRole("heading", { level: 3 })).toBeInTheDocument();
      expect(
        within(head as HTMLElement).getByRole("button", { name: "Show evidence" }),
      ).toBeInTheDocument();
      expect(li.querySelector("details.finding-more")).not.toHaveAttribute("open");
    }
    // a probable finding keeps saying so in its first word
    expect(screen.getByRole("heading", { level: 3, name: /^Probable: / })).toBeInTheDocument();
  });

  it("two clicks from the run page: the Findings tab, then the finding, shows its evidence in the timeline", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = withFindings(failingChainRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(screen.getByRole("tab", { name: "Findings" })); // click 1
    const chain = (await screen.findAllByTestId("finding")).find(
      (i) => i.dataset.type === "failure_chain",
    )!;
    await user.click(within(chain).getByRole("button", { name: "Show evidence" })); // click 2
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("shell.command");
    expect(screen.getByRole("tab", { name: "Timeline" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("option", { selected: true })).toBeInTheDocument();
  });

  it("an analysed run with nothing wrong says so, and the health dimensions are no longer on the page", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = withFindings(successRun());
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    expect(await screen.findByTestId("findings-empty")).toHaveTextContent("Nothing wrong found.");
    expect(container.querySelector("details.health-disclosure, [data-testid=health]")).toBeNull();
    expect(screen.queryByText(/Health/)).toBeNull();
    expect(within(screen.getByRole("tab", { name: "Findings" })).queryByText(/^\d+$/)).toBeNull();
  });

  it("a run with one finding counts 1 in the tab", async () => {
    stubApi();
    const { run } = withFindings(loopingRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const tab = screen.getByRole("tab", { name: "Findings" });
    expect(await within(tab).findByText("1")).toBeInTheDocument();
  });
});
