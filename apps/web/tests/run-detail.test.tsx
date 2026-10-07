import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail, headline } from "@/components/RunDetail";
import { EventDrawer, drawerKind } from "@/components/EventDrawer";
import {
  expensiveRun,
  failureRetryRun,
  runningRun,
  successRun,
  approvalRun,
} from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const rowsText = () => screen.getAllByRole("option").map((o) => o.textContent ?? "");

describe("RunDetail", () => {
  it("shows loading, then the run header, summary and timeline in API order", async () => {
    stubApi();
    const { run, events } = successRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading run");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Fix login session expiry" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Success")).toBeInTheDocument();
    expect(screen.getByTestId("headline")).toHaveTextContent("Succeeded in 11 s");
    await waitFor(() =>
      expect(screen.getByTestId("progress")).toHaveTextContent(
        `${events.length} of ${events.length} events shown`,
      ),
    );
    const types = screen
      .getAllByRole("option")
      .map((o) => o.querySelector(".tl-type")?.textContent)
      .filter(Boolean);
    expect(types).toEqual(events.map((e) => e.event_type));
    expect(screen.queryByTestId("first-error")).toBeNull();
  });

  it("emphasises the first causal error and the retries on a failed run", async () => {
    stubApi();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const banner = await screen.findByTestId("first-error");
    expect(banner).toHaveTextContent("run_migration");
    expect(screen.getByTestId("headline")).toHaveTextContent("Failed after 2 retries");
    await waitFor(() => expect(document.querySelectorAll("[data-first-error]")).toHaveLength(1));
    expect(document.querySelectorAll("[data-retry]")).toHaveLength(2);
    // Non-colour cues: the text "First error" and "Retry" tags exist.
    expect(
      within(document.querySelector("[data-first-error]") as HTMLElement).getByText(/First error/),
    ).toBeInTheDocument();
  });

  it("filters by class and errors-only, and groups collapse", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    await screen.findByTestId("first-error");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("15 of 15"));
    await user.click(screen.getByLabelText("Errors only"));
    expect(screen.getByTestId("progress")).toHaveTextContent("4 of 15");
    expect(rowsText().every((t) => /error|failed/.test(t))).toBe(true);
    await user.click(screen.getByLabelText("Errors only"));
    await user.click(screen.getByLabelText("Model calls"));
    expect(rowsText().some((t) => t.includes("llm.request"))).toBe(false);
    await user.click(screen.getByLabelText("Model calls"));
    const before = screen.getAllByRole("option").length;
    await user.click(screen.getByRole("button", { name: "Collapse groups" }));
    expect(screen.getAllByRole("option").length).toBeLessThan(before);
    await user.click(screen.getByRole("button", { name: "Expand groups" }));
    expect(screen.getAllByRole("option").length).toBe(before);
  });

  it("shows an empty state when filters hide everything and recovers", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={successRun().run.id} base={BASE} />);
    await screen.findByTestId("headline");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    await user.click(screen.getByLabelText("Errors only"));
    expect(screen.getByText("No events match these filters")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getAllByRole("option").length).toBeGreaterThan(5);
  });

  it("keyboard: j/k move, Enter opens the drawer, Escape closes and returns focus", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={successRun().run.id} base={BASE} />);
    const list = await screen.findByRole("listbox");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(3));
    list.focus();
    await user.keyboard("jj");
    expect(screen.getAllByRole("option", { selected: true })).toHaveLength(1);
    expect(screen.getByRole("option", { selected: true })).toHaveTextContent("agent.started");
    await user.keyboard("k");
    expect(screen.getByRole("option", { selected: true })).toHaveTextContent("run.started");
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("run.started");
    expect(within(dialog).getByRole("button", { name: "Close details" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(list).toHaveFocus());
  });

  it("e jumps to the first error and opens the error drawer", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const list = await screen.findByRole("listbox");
    list.focus();
    await user.keyboard("e");
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("data-error");
    expect(within(dialog).getByRole("heading", { name: /Error/ })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("run_migration failed: ConnectionTimeout");
  });

  it.each([
    ["running", runningRun, /In progress/],
    ["waiting", approvalRun, /Waiting for approval/],
    ["expensive", expensiveRun, /Succeeded/],
  ])("renders a %s run", async (_n, build, text) => {
    stubApi();
    renderWithQuery(<RunDetail runId={build().run.id} base={BASE} />);
    expect(await screen.findByTestId("headline")).toHaveTextContent(text);
  });

  it("shows a not-found state and a retryable error state with the request id", async () => {
    stubApi();
    const first = renderWithQuery(<RunDetail runId="run_00000000000000000000000999" base={BASE} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found");
    first.unmount();

    stubApi({
      "/v1/runs/run_00000000000000000000000001": () =>
        Response.json(
          {
            error: {
              code: "DEPENDENCY_UNAVAILABLE",
              message: "Database unavailable.",
              category: "X",
              retryable: true,
              request_id: "req_abc",
              details: {},
            },
          },
          { status: 503 },
        ),
    });
    renderWithQuery(<RunDetail runId="run_00000000000000000000000001" base={BASE} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Database unavailable.");
    expect(alert).toHaveTextContent("req_abc");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("flags a summary that is still processing", async () => {
    stubApi({
      "/v1/runs/run_00000000000000000000000001": () =>
        Response.json({ ...successRun().run, summary_state: "processing" }),
    });
    renderWithQuery(<RunDetail runId="run_00000000000000000000000001" base={BASE} />);
    expect(await screen.findByText(/Summary is updating/)).toBeInTheDocument();
  });
});

describe("headline", () => {
  it("covers every terminal and active state", () => {
    const run = successRun().run;
    expect(headline({ ...run, status: "CANCELLED" }, null, null)).toMatch(/^Cancelled/);
    expect(headline({ ...run, status: "TIMED_OUT" }, "x", 1000)).toMatch(
      /^Timed out.*First error at \+1\.0s: x/,
    );
    expect(headline({ ...run, status: "BLOCKED" }, null, null)).toMatch(/^Blocked/);
    expect(headline({ ...run, status: "QUEUED" }, null, null)).toMatch(/^In progress/);
  });
});

describe("EventDrawer kinds", () => {
  it("chooses llm, tool and generic", () => {
    const e = successRun().events;
    expect(drawerKind(e.find((x) => x.event_type === "llm.request.completed")!)).toBe("llm");
    expect(drawerKind(e.find((x) => x.event_type === "tool.call.completed")!)).toBe("tool");
    expect(drawerKind(e.find((x) => x.event_type === "file.read")!)).toBe("generic");
  });

  it("renders llm fields, tool fields, generic attributes and never interprets payload markup", async () => {
    stubApi();
    const { run, events } = successRun();
    const llm = events.find((x) => x.event_type === "llm.request.completed")!;
    const { unmount } = renderWithQuery(
      <EventDrawer runId={run.id} event={llm} onClose={() => {}} />,
    );
    const d = screen.getByRole("dialog");
    expect(d).toHaveAttribute("data-kind", "llm");
    expect(d).toHaveTextContent("Input tokens");
    expect(d).toHaveTextContent("1,840");
    expect(await screen.findByLabelText("Event payload")).toHaveTextContent(
      "I will read the auth module",
    );
    unmount();

    const tool = events.find((x) => x.event_type === "tool.call.completed")!;
    renderWithQuery(<EventDrawer runId={run.id} event={tool} onClose={() => {}} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("search_code");
    expect(screen.getByText("No payload was captured for this event.")).toBeInTheDocument();
  });

  it("renders hostile payload and attribute content as text", async () => {
    const run = successRun();
    const evil = {
      ...run.events.find((x) => x.has_payload)!,
      attributes: { "x.html": "<img src=x onerror=alert(1)>" },
    };
    stubApi({
      [`/events/${evil.event_id}`]: () =>
        Response.json({ ...evil, payload: { note: "<script>alert(1)</script>" } }),
    });
    renderWithQuery(<EventDrawer runId={run.run.id} event={evil} onClose={() => {}} />);
    expect(await screen.findByLabelText("Event payload")).toHaveTextContent(
      "<script>alert(1)</script>",
    );
    expect(document.querySelector("script, img")).toBeNull();
  });

  it("shows a payload error with retry", async () => {
    const run = successRun();
    const withPayload = run.events.find((x) => x.has_payload)!;
    stubApi({
      [`/events/${withPayload.event_id}`]: () =>
        Response.json(
          { error: { code: "X", message: "boom", request_id: "r1", retryable: true } },
          { status: 500 },
        ),
    });
    renderWithQuery(<EventDrawer runId={run.run.id} event={withPayload} onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
