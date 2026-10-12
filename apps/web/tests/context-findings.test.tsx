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

describe("findings in the context panel (Phase 22 D3)", () => {
  it("the closed Context button counts the findings; each finding is one line with the way to its evidence", async () => {
    stubApi();
    const { run } = withFindings(failingChainRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const toggle = screen.getByRole("button", { name: /^Context/ });
    expect(await screen.findByTestId("context-summary")).toHaveTextContent("· 2 findings");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
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

  it("two clicks from the run page: Context, then the finding, shows its evidence", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = withFindings(failingChainRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(screen.getByRole("button", { name: /^Context/ })); // click 1
    const chain = (await screen.findAllByTestId("finding")).find(
      (i) => i.dataset.type === "failure_chain",
    )!;
    await user.click(within(chain).getByRole("button", { name: "Show evidence" })); // click 2
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("shell.command");
    expect(screen.getByRole("option", { selected: true })).toBeInTheDocument();
  });

  it("health sits in a closed disclosure; an analysed run with nothing wrong says so", async () => {
    stubApi();
    const { run } = withFindings(successRun());
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(await screen.findByTestId("findings-empty")).toHaveTextContent("Nothing wrong found.");
    expect(container.querySelector("details.health-disclosure")).not.toHaveAttribute("open");
    expect(screen.queryByTestId("context-summary")).toBeNull();
  });

  it("a run with one finding says '1 finding'", async () => {
    stubApi();
    const { run } = withFindings(loopingRun());
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    expect(await screen.findByTestId("context-summary")).toHaveTextContent("· 1 finding");
  });
});
