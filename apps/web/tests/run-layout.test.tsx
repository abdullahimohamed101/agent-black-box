import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { failureRetryRun, multiAgentRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(""),
}));

/** jsdom has no matchMedia; the context column asks it whether the window is narrower than 100rem. */
function stubNarrow(narrow: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: narrow,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("run work area", () => {
  it("lays out header, tab row and a work area with the context column, pane and no inspector", async () => {
    stubApi();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(container.querySelector(".run-detail.app-page")).not.toBeNull();
    const work = container.querySelector(".work")!;
    expect(work).toHaveAttribute("data-inspector", "closed");
    expect(work).toHaveAttribute("data-context", "open");
    // the four sections live in the context column, in this order, the timeline in the pane
    const ctx = screen.getByRole("complementary", { name: "Run context" });
    const headings = within(ctx)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(headings).toEqual(expect.arrayContaining(["Findings", "Evaluations"]));
    expect(headings.indexOf("Findings")).toBeLessThan(headings.indexOf("Evaluations"));
    expect(within(ctx).queryByRole("listbox")).toBeNull();
    expect(container.querySelector(".pane")).toContainElement(
      screen.getByRole("listbox", { name: /Run timeline/ }),
    );
  });

  it("puts the agent tree in the context column of a multi-agent run", async () => {
    stubApi();
    const { run } = multiAgentRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const ctx = await screen.findByRole("complementary", { name: "Run context" });
    expect(await within(ctx).findByTestId("agent-tree")).toBeInTheDocument();
  });

  it("the view switch is a group of native radios", async () => {
    stubApi();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const group = screen.getByRole("group", { name: "View" });
    expect(within(group).getAllByRole("radio")).toHaveLength(3);
  });

  it("on a narrow window the inspector folds the context to a strip; the strip and the toggle bring it back", async () => {
    stubApi();
    stubNarrow(true);
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const toggle = screen.getByRole("button", { name: "Context" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    await screen.findByRole("dialog");
    const work = container.querySelector(".work")!;
    expect(work).toHaveAttribute("data-inspector", "open");
    expect(work).toHaveAttribute("data-context", "closed");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByRole("button", { name: "Show run context" }));
    expect(work).toHaveAttribute("data-context", "open");
    await user.click(toggle);
    expect(work).toHaveAttribute("data-context", "closed");
    await user.click(toggle);
    expect(work).toHaveAttribute("data-context", "open");
  });

  it("on a wide window the context stays open beside the inspector", async () => {
    stubApi();
    stubNarrow(false);
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    await screen.findByRole("dialog");
    expect(container.querySelector(".work")).toHaveAttribute("data-context", "open");
  });

  it("the inspector's edge is a keyboard-operable separator that sets the pane width", async () => {
    stubApi();
    stubNarrow(false);
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.queryByRole("separator")).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Show it" }));
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

  it("the scrolling context column is a tab stop while open, and not while folded to a strip", async () => {
    stubApi();
    stubNarrow(true);
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const ctx = screen.getByRole("complementary", { name: "Run context" });
    expect(ctx).toHaveAttribute("tabindex", "0");
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    await screen.findByRole("dialog");
    expect(ctx).not.toHaveAttribute("tabindex");
  });
});
