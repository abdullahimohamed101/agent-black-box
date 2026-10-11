import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { failureRetryRun, multiAgentRun, successRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// The run page reads `?view=` through next/navigation; each test sets what the URL says.
const nav = vi.hoisted(() => ({ query: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(nav.query),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  nav.query = "";
});

describe("RunDetail view switch", () => {
  it("starts on the timeline, switches to the waterfall and back, and keeps the choice in the URL", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.getByRole("heading", { name: "Timeline" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Timeline" })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: "Waterfall" }));
    expect(await screen.findByRole("treegrid")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Waterfall" })).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(window.location.search).toContain("view=waterfall");

    await user.click(screen.getByRole("radio", { name: "Timeline" }));
    expect(await screen.findByRole("listbox", { name: /Run timeline/ })).toBeInTheDocument();
    expect(window.location.search).not.toContain("view=");
  });

  it("honours ?view=waterfall on load", async () => {
    stubApi();
    nav.query = "view=waterfall";
    const { run } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    expect(await screen.findByRole("treegrid")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Waterfall" })).toBeChecked();
  });

  it("ignores an unknown view value", async () => {
    stubApi();
    nav.query = "view=%3Cscript%3E";
    const { run } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    expect(await screen.findByRole("listbox", { name: /Run timeline/ })).toBeInTheDocument();
  });

  it("selection is shared: a waterfall row selects the same event in the timeline", async () => {
    stubApi();
    const user = userEvent.setup();
    nav.query = "view=waterfall";
    const { run, events } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const grid = await screen.findByRole("treegrid");
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(`${events.length} events in`),
    );
    const tool = within(grid)
      .getAllByRole("row")
      .find((r) => r.textContent?.includes("search_code"))!;
    await user.click(tool);
    // clicking activates: the drawer opens on the span's opener event
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("radio", { name: "Timeline" }));
    const selected = await screen.findByRole("option", { selected: true });
    expect(selected).toHaveTextContent(/tool\.call\.started/);
  });

  it("e jumps to the first error in the waterfall and opens the drawer", async () => {
    stubApi();
    const user = userEvent.setup();
    nav.query = "view=waterfall";
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const grid = await screen.findByRole("treegrid");
    await screen.findByTestId("first-error");
    grid.focus();
    await user.keyboard("e");
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("ConnectionTimeout");
    await waitFor(() =>
      expect(
        within(screen.getByRole("treegrid"))
          .getAllByRole("row")
          .some((r) => r.getAttribute("aria-selected") === "true"),
      ).toBe(true),
    );
  });

  it("a multi-agent run shows the agent tree, agent chips, and an agent filter that narrows the timeline", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run, events } = multiAgentRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const tree = await screen.findByTestId("agent-tree");
    expect(within(tree).getByRole("heading", { name: "Agents (5)" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(
        `${events.length} of ${events.length}`,
      ),
    );
    expect(document.querySelectorAll(".tl-agent").length).toBeGreaterThan(0);
    await user.click(within(tree).getByRole("button", { name: /^coder/ }));
    const coderEvents = events.filter((e) => e.agent_id === "coder").length;
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(
        `${coderEvents} of ${events.length}`,
      ),
    );
    await user.click(within(tree).getByRole("button", { name: "Show all agents" }));
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(
        `${events.length} of ${events.length}`,
      ),
    );
  });

  it("a single-agent run has no agent tree", async () => {
    stubApi();
    const { run } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    expect(screen.queryByTestId("agent-tree")).toBeNull();
    expect(document.querySelector(".tl-agent")).toBeNull();
  });

  it("the failure run's root span is finished in the waterfall", async () => {
    stubApi();
    nav.query = "view=waterfall";
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const grid = await screen.findByRole("treegrid");
    await waitFor(() => expect(within(grid).getAllByRole("row").length).toBeGreaterThan(2));
    expect(within(grid).queryByText("no end recorded")).toBeNull();
  });
});
