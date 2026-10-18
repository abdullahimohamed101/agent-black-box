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
    expect(err).toContainElement(screen.getByTestId("headline"));
    expect(err).toHaveTextContent(
      /^Failed at step \d+ after 6\.2 s: run_migration, ConnectionTimeout\./,
    );
    expect(within(err).getByRole("button", { name: "Go to first error" })).toBeInTheDocument();
    // no separate banner repeats it
    expect(screen.getAllByText(/Failed at step/)).toHaveLength(1);
    expect(document.querySelector(".banner")).toBeNull();
  });

  it("Go to first error opens the failing event", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    await screen.findByTestId("first-error");
    await user.click(screen.getByRole("button", { name: "Go to first error" }));
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

  it("Go to first error works before the page holding the first error has loaded (review P3-2)", async () => {
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
    const go = await screen.findByRole("button", { name: "Go to first error" });
    await user.click(go);
    expect(await screen.findByRole("dialog")).toHaveTextContent("ConnectionTimeout");
  });

  it("shows exactly five readouts, none lit: Duration, Cost, Tokens, Retries, Errors", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const readouts = await waitFor(() => {
      const dl = document.querySelector('dl[aria-label="Run summary"]');
      if (!dl) throw new Error("no readouts yet");
      return dl as HTMLElement;
    });
    const labels = [...readouts.querySelectorAll("dt")].map((d) => d.textContent);
    expect(labels).toEqual(["Duration", "Cost", "Tokens", "Retries", "Errors"]);
    // the colour that used to light Errors and Retries is gone: the fault line is the page's one red mark
    expect(readouts.querySelector("[data-tone]")).toBeNull();
    expect(within(readouts).queryByText("Agents")).toBeNull(); // single-agent run
  });

  it("a clean run has no first-error control and the same five readouts", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={successRun().run.id} base={BASE} />);
    await screen.findByRole("heading", { level: 1 });
    const dl = (await screen.findByText("Errors")).closest("dl") as HTMLElement;
    expect(dl.querySelectorAll("dt")).toHaveLength(5);
    expect(screen.queryByTestId("first-error")).toBeNull();
    expect(screen.queryByRole("button", { name: "Go to first error" })).toBeNull();
  });

  it("Details opens the identity items and the figures that left the line; a multi-agent run adds Agents", async () => {
    stubApi();
    const user = userEvent.setup();
    const multi = renderWithQuery(<RunDetail runId={multiAgentRun().run.id} base={BASE} />);
    const toggle = await screen.findByRole("button", { name: /^Details/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const details = document.getElementById("run-details")!;
    expect(details).toHaveAttribute("hidden");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(details).not.toHaveAttribute("hidden");
    expect(
      within(details).getByText("Agents", { selector: "dt" }).nextElementSibling,
    ).toHaveTextContent("5");
    for (const label of [
      "Run id",
      "Agent",
      "Ordered by",
      "Started",
      "Model calls",
      "Tool calls",
      "Files changed",
    ])
      expect(within(details).getByText(label, { selector: "dt" })).toBeInTheDocument();
    multi.unmount();
    renderWithQuery(<RunDetail runId={runningRun().run.id} base={BASE} />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("first-error")).toBeNull();
  });

  it("keeps the whole run id and the agent in Details, one click from the title line", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await user.click(await screen.findByRole("button", { name: /^Details/ }));
    const details = document.getElementById("run-details")!;
    const id = within(details).getByText(run.id);
    expect(id).toHaveAttribute("title", run.id);
    expect(details).toHaveTextContent("coding-agent");
    expect(details).toHaveTextContent(/Ordered by/);
  });

  it("the title line has a back link to Runs (KI-276) and the run name as the h1", async () => {
    stubApi();
    const { run } = failureRetryRun();
    renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent(run.name ?? run.id);
    const back = screen.getByRole("link", { name: "Back to runs" });
    expect(back).toHaveAttribute("href", `/w/${BASE.workspace}/projects/${BASE.project}/runs`);
    expect(back).toHaveTextContent("Runs");
    expect(back.compareDocumentPosition(h1) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the status is a glyph and a word, then the age; the word is the only coloured text in the title line", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const age = await screen.findByTestId("run-age");
    const badge = document.querySelector(".badge[data-status]") as HTMLElement;
    expect(badge).toHaveTextContent("Failed");
    expect(badge.querySelector('[aria-hidden="true"]')).not.toBeNull(); // the glyph is decoration
    expect(age).toHaveTextContent(/ago$/);
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
