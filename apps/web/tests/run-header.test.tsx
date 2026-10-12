import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { headlineModel } from "@/lib/runHeadline";
import {
  approvalRun,
  failureRetryRun,
  multiAgentRun,
  runningRun,
  successRun,
} from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("run header", () => {
  it("states the first error once, inside the headline, with the control beside it", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const err = await screen.findByTestId("first-error");
    expect(screen.getByTestId("headline")).toContainElement(err);
    expect(err).toHaveTextContent(
      /^Failed at step \d+ \(\+6\.2 s\): run_migration, ConnectionTimeout/,
    );
    expect(within(err).getByRole("button", { name: "Show it" })).toBeInTheDocument();
    // no separate banner repeats it
    expect(screen.getAllByText(/Failed at step/)).toHaveLength(1);
    expect(document.querySelector(".banner")).toBeNull();
  });

  it("Show it opens the failing event", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    await screen.findByTestId("first-error");
    await user.click(screen.getByRole("button", { name: "Show it" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("ConnectionTimeout");
  });

  it("an approval wait keeps counting while the tab stays open (review P1-2)", async () => {
    const f = approvalRun();
    const ask = f.events.find((e) => e.event_type === "approval.requested")!;
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(Date.parse(ask.occurred_at) + 4 * 60_000);
    stubApi();
    renderWithQuery(<RunDetail runId={f.run.id} base={BASE} />);
    const detail = await screen.findByTestId("headline-detail");
    expect(detail).toHaveTextContent(/waiting 4m 0\ds/);
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(screen.getByTestId("headline-detail")).toHaveTextContent(/waiting 4m (2|3)\ds/);
  });

  it("Show it works before the page holding the first error has loaded (review P3-2)", async () => {
    const f = failureRetryRun();
    const errId = f.run.first_error!.event_id;
    const cut = f.events.findIndex((e) => e.event_id === errId);
    const user = userEvent.setup();
    stubApi({
      "/events": () =>
        Response.json({
          items: f.events.slice(0, cut),
          next_cursor: null,
          ordering_mode: "sequence",
        }),
    });
    renderWithQuery(<RunDetail runId={f.run.id} base={BASE} />);
    const go = await screen.findByRole("button", { name: "Show it" });
    await user.click(go);
    expect(await screen.findByRole("dialog")).toHaveTextContent("ConnectionTimeout");
  });

  it("lights Errors and Retries only when they are non-zero, in the readout strip", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const readouts = await waitFor(() => {
      const dl = document.querySelector('dl[aria-label="Run summary"]');
      if (!dl) throw new Error("no readouts yet");
      return dl as HTMLElement;
    });
    const stat = (label: string) =>
      within(readouts).getByText(label).closest(".stat") as HTMLElement;
    expect(stat("Errors")).toHaveAttribute("data-tone", "bad");
    expect(stat("Retries")).toHaveAttribute("data-tone", "warn");
    expect(stat("Duration")).not.toHaveAttribute("data-tone");
    expect(within(readouts).queryByText("Agents")).toBeNull(); // single-agent run
  });

  it("a clean run has no tone and no first-error control", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={successRun().run.id} base={BASE} />);
    await screen.findByRole("heading", { level: 1 });
    const dl = (await screen.findByText("Errors")).closest("dl") as HTMLElement;
    for (const label of ["Errors", "Retries"])
      expect(within(dl).getByText(label).closest(".stat")).not.toHaveAttribute("data-tone");
    expect(screen.queryByTestId("first-error")).toBeNull();
    expect(screen.queryByRole("button", { name: "Show it" })).toBeNull();
  });

  it("a multi-agent run adds an Agents readout; a running run has no error control", async () => {
    stubApi();
    const multi = renderWithQuery(<RunDetail runId={multiAgentRun().run.id} base={BASE} />);
    const dl = (await screen.findByText("Agents", { selector: "dt" })).closest("dl") as HTMLElement;
    expect(within(dl).getByText("Agents").closest(".stat")).toHaveTextContent("5");
    multi.unmount();
    renderWithQuery(<RunDetail runId={runningRun().run.id} base={BASE} />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("first-error")).toBeNull();
  });

  it("puts the id line next to the title and keeps the whole id in its tooltip", async () => {
    stubApi();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const id = await waitFor(() => {
      const el = document.querySelector<HTMLElement>(".run-title .id");
      if (!el) throw new Error("not yet");
      return el;
    });
    expect(id).toHaveAttribute("title", run.id);
    expect(id).toHaveTextContent("coding-agent");
    expect(id).toHaveTextContent(/ordered by/);
  });
});

describe("headline text per status", () => {
  const f = successRun();
  const model = (status: (typeof f.run)["status"]) =>
    headlineModel({
      run: { ...f.run, status },
      events: f.events,
      t0: Date.parse(f.run.started_at),
      eventsComplete: true,
    });
  it("one lead sentence per status, never different text from the model", () => {
    expect(model("SUCCESS").lead).toMatch(/^Completed in/);
    expect(model("FAILED").lead).toMatch(/^Failed after/);
    expect(model("TIMED_OUT").lead).toMatch(/^Timed out after/);
    expect(model("BLOCKED").lead).toMatch(/^Blocked after/);
    expect(model("CANCELLED").lead).toMatch(/^Cancelled at step/);
    expect(model("WAITING").lead).toMatch(/^Waiting/);
    expect(model("RUNNING").lead).toMatch(/^Running/);
    expect(model("QUEUED").lead).toMatch(/^Queued/);
  });
  it("has no failure, so no jump, without an error", () => {
    expect(model("SUCCESS").failure).toBeNull();
  });
});
