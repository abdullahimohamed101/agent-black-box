import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { wrongFixCodingRun } from "@/fixtures/codingRuns";
import { failureRetryRun, multiAgentRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(""),
}));

/** jsdom has no matchMedia; `wide` answers the query that decides between the Steps column and the Steps strip. */
function stubMedia(wide: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: wide && query.includes("min-width: 1100px"),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("run work area", () => {
  it("lays out header, tab row and a work area with the Steps column, the pane and no inspector", async () => {
    stubApi();
    stubMedia(true);
    const { run } = wrongFixCodingRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(container.querySelector(".run-detail.app-page")).not.toBeNull();
    const work = container.querySelector(".work")!;
    expect(work).toHaveAttribute("data-inspector", "closed");
    expect(work).toHaveAttribute("data-steps", "open");
    // the left column is the agent's steps and nothing else: no findings, no health, no evaluations
    const steps = screen.getByRole("complementary", { name: "Steps" });
    expect(within(steps).getByRole("heading", { level: 2, name: "Steps" })).toBeInTheDocument();
    expect(
      within(steps).queryByRole("heading", { name: /Findings|Evaluations|Health/ }),
    ).toBeNull();
    expect(within(steps).queryByRole("listbox")).toBeNull();
    expect(container.querySelector(".pane")).toContainElement(
      screen.getByRole("listbox", { name: /Run timeline/ }),
    );
    expect(screen.queryByRole("complementary", { name: "Run context" })).toBeNull();
  });

  it("below 1100px the Steps column is one disclosure strip above the pane, with the counts", async () => {
    stubApi();
    stubMedia(false);
    const { run } = wrongFixCodingRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const strip = container.querySelector("details.steps-strip")!;
    expect(strip).not.toBeNull();
    expect(strip.querySelector("summary")).toHaveTextContent(/^Steps · \d+ \(\d+ failed\)$/);
    expect(strip).not.toHaveAttribute("open");
    // the strip comes before the pane in the reading order
    const pane = container.querySelector(".pane")!;
    expect(strip.compareDocumentPosition(pane) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a run with neither a coding story nor several agents has no Steps column at all", async () => {
    stubApi();
    stubMedia(true);
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(container.querySelector(".steps")).toBeNull();
    expect(container.querySelector(".work")).toHaveAttribute("data-steps", "none");
  });

  it("puts the agent tree in the Steps column of a multi-agent run", async () => {
    stubApi();
    stubMedia(true);
    const { run } = multiAgentRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const aside = await screen.findByRole("complementary", { name: /Steps|Agents/ });
    expect(await within(aside).findByTestId("agent-tree")).toBeInTheDocument();
  });

  it("the views are one tablist of five tabs, the timeline selected first; no radios", async () => {
    stubApi();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const list = screen.getByRole("tablist", { name: "Run views" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.replace(/\d+$/, "").trim())).toEqual([
      "Timeline",
      "Waterfall",
      "Evidence",
      "Findings",
      "Evaluations",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    // the pane is the tab's panel
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[0]!.id);
  });

  it("Findings and Evaluations are tabs of the pane, not sections of a column", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const panel = screen.getByRole("tabpanel");
    expect(await within(panel).findByRole("heading", { level: 2, name: "Findings" })).toBeVisible();
    expect(within(panel).queryByRole("listbox")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Evaluations" }));
    expect(
      await within(screen.getByRole("tabpanel")).findByRole("heading", {
        level: 2,
        name: "Evaluations",
      }),
    ).toBeVisible();
  });

  it("the inspector's edge is a keyboard-operable separator that sets the pane width", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.queryByRole("separator")).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Go to first error" }));
    await screen.findByRole("dialog");
    const sep = screen.getByRole("separator", { name: "Resize the inspector" });
    sep.focus();
    await user.keyboard("{ArrowLeft}");
    const w = (container.querySelector(".work") as HTMLElement).style.getPropertyValue("--insp-w");
    expect(w).toMatch(/^\d+px$/);
    await user.keyboard("{ArrowRight}");
    expect(
      (container.querySelector(".work") as HTMLElement).style.getPropertyValue("--insp-w"),
    ).not.toBe(w);
    // Escape in the dialog still closes it and the separator goes with it
    await user.keyboard("{Escape}");
  });

  it("the inspector opens beside an open Steps column without folding it (no context toggle exists any more)", async () => {
    stubApi();
    stubMedia(true);
    const user = userEvent.setup();
    const { run } = wrongFixCodingRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.queryByRole("button", { name: /^Context/ })).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Go to first error" }));
    await screen.findByRole("dialog");
    const work = container.querySelector(".work")!;
    expect(work).toHaveAttribute("data-inspector", "open");
    expect(work).toHaveAttribute("data-steps", "open");
    await waitFor(() =>
      expect(screen.getByRole("complementary", { name: "Steps" })).toBeInTheDocument(),
    );
  });
});

describe("the Steps strip", () => {
  it("is a native disclosure: activating its summary opens it and focus stays on the summary, never on <body>", async () => {
    stubApi();
    stubMedia(false);
    const user = userEvent.setup();
    const { run } = wrongFixCodingRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const summary = container.querySelector<HTMLElement>("details.steps-strip > summary")!;
    await user.click(summary);
    expect(container.querySelector("details.steps-strip")).toHaveAttribute("open");
    expect(summary).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);
  });
});
