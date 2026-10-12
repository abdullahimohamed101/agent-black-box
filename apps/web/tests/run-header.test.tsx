import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail, headline, headlineParts } from "@/components/RunDetail";
import { failureRetryRun, multiAgentRun, runningRun, successRun } from "@/fixtures/runs";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("run header", () => {
  it("states the first error once, inside the headline, with the control beside it", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={failureRetryRun().run.id} base={BASE} />);
    const err = await screen.findByTestId("first-error");
    expect(screen.getByTestId("headline")).toContainElement(err);
    expect(err).toHaveTextContent(/^First error at \+6\.2s: run_migration/);
    expect(within(err).getByRole("button", { name: "Show it" })).toBeInTheDocument();
    // no separate banner repeats it
    expect(screen.getAllByText(/First error at/)).toHaveLength(1);
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

describe("headlineParts", () => {
  const run = successRun().run;
  it("is the headline cut in three, never different text", () => {
    for (const status of [
      "SUCCESS",
      "FAILED",
      "TIMED_OUT",
      "CANCELLED",
      "WAITING",
      "RUNNING",
    ] as const) {
      const r = { ...run, status };
      const p = headlineParts(r, "tool_x · Boom", 4600);
      expect(p.lead + p.recovered + (p.error ?? "")).toBe(headline(r, "tool_x · Boom", 4600));
      expect(p.error).toMatch(/^First error at \+4\.6s: tool_x · Boom\.$/);
    }
  });
  it("a recovered success keeps the 'It recovered from an error.' clause before the error", () => {
    const p = headlineParts({ ...run, status: "SUCCESS" }, "x", 1000);
    expect(p.recovered).toContain("It recovered from an error.");
  });
  it("has no error part without an error", () => {
    expect(headlineParts(run, null, null)).toEqual({
      lead: headline(run, null, null),
      recovered: "",
      error: null,
    });
  });
});
