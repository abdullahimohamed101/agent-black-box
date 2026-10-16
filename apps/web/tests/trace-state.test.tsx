import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Analytics } from "@/components/Analytics";
import { CompareView } from "@/components/CompareView";
import { RunDetail } from "@/components/RunDetail";
import { RunsTable } from "@/components/RunsTable";
import { ErrorState } from "@/components/States";
import { TraceNotice } from "@/components/TraceNotice";
import { fixtureAnalytics } from "@/fixtures/analytics";
import { fixtureReply } from "@/fixtures/api";
import { successRun } from "@/fixtures/runs";
import { DELETING_RUN_ID, EXPIRED_RUN_ID } from "@/fixtures/traceStateRuns";
import { ApiRequestError } from "@/lib/api/client";
import type { RunOut } from "@/lib/api/types";
import { BASE, renderWithQuery, stubApi } from "./helpers";

let search = "";
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(search),
}));

beforeEach(() => {
  search = "";
});
afterEach(() => vi.unstubAllGlobals());

const run = (id: string): RunOut => {
  const r = fixtureReply(["v1", "runs", id], new URLSearchParams()).body as RunOut;
  return r;
};

describe("a run whose trace the retention policy removed", () => {
  it("expired: says so in words, keeps the summary numbers, shows no tabs and asks for no trace detail", async () => {
    const calls = stubApi();
    renderWithQuery(<RunDetail runId={EXPIRED_RUN_ID} base={BASE} />);
    const notice = await screen.findByTestId("trace-notice");
    expect(notice).toHaveAttribute("data-state", "expired");
    expect(notice).toHaveTextContent(
      "The trace of this run was removed by the retention policy; the run summary and its numbers remain.",
    );
    expect(notice).toHaveAttribute("role", "note");
    expect(screen.getByRole("heading", { level: 1, name: "Nightly revenue report" })).toBeVisible();
    // the numbers of the record are still there
    expect(screen.getByText("Tool calls").closest("div")).toHaveTextContent("1");
    expect(screen.getByText("Tokens").closest("div")).toHaveTextContent("1,200 in / 180 out");
    // no tabs, no empty or error state of the trace
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: /Replay/ })).toBeNull();
    expect(screen.queryByText("This run has no events yet.")).toBeNull();
    expect(screen.queryByTestId("headline")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("progress")).toBeNull();
    // nothing of the trace is requested once the run says it is gone (KI-198)
    await new Promise((r) => setTimeout(r, 50));
    const trace = calls.filter(
      (c) => /\/(findings|evaluations|spans|stream|compare)\b/.test(c) || /cursor=/.test(c),
    );
    expect(trace).toEqual([]);
    expect(calls.filter((c) => /\/events\b/.test(c)).length).toBeLessThanOrEqual(1);
  });

  it("deleting: says it is being removed, with the same shared notice", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={DELETING_RUN_ID} base={BASE} />);
    const notice = await screen.findByTestId("trace-notice");
    expect(notice).toHaveAttribute("data-state", "deleting");
    expect(notice).toHaveTextContent("The retention policy is removing the trace of this run.");
    expect(notice).toHaveTextContent("the run summary and its numbers remain");
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("available: no notice, the timeline as before", async () => {
    stubApi();
    renderWithQuery(<RunDetail runId={successRun().run.id} base={BASE} />);
    await screen.findByTestId("progress");
    expect(screen.queryByTestId("trace-notice")).toBeNull();
  });

  it("the shared notice and marker are text, with a glyph that is hidden from assistive tech", () => {
    const { container } = render(<TraceNotice state="expired" />);
    expect(container.querySelector("[aria-hidden=true]")).not.toBeNull();
    expect(container.querySelector("script, img")).toBeNull();
  });
});

describe("the runs list", () => {
  it("marks expired and deleting runs with a glyph and words, keeps them openable, and leaves others alone", () => {
    const runs = [run(EXPIRED_RUN_ID), run(DELETING_RUN_ID), successRun().run];
    render(<RunsTable runs={runs} base={BASE} caption="Runs" />);
    const markers = screen.getAllByTestId("trace-marker");
    expect(markers.map((m) => [m.getAttribute("data-state"), m.textContent])).toEqual([
      ["expired", "◇ Trace expired"],
      ["deleting", "↻ Trace being removed"],
    ]);
    for (const m of markers) expect(m.querySelector("[aria-hidden=true]")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Nightly revenue report" })).toHaveAttribute(
      "href",
      expect.stringContaining(EXPIRED_RUN_ID),
    );
    expect(within(screen.getAllByRole("row")[3]!).queryByTestId("trace-marker")).toBeNull();
  });
});

describe("errors in words", () => {
  it("a 404 for a run says it no longer exists, without saying why it is gone", () => {
    render(
      <ErrorState
        error={new ApiRequestError("Run not found.", 404, "RUN_NOT_FOUND", "req_1", false)}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This run no longer exists");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "removed by the retention policy, or the link",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Not found");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("a 409 TRACE_EXPIRED is a statement, not an error: no alert, no retry", () => {
    render(
      <ErrorState
        error={new ApiRequestError("x", 409, "TRACE_EXPIRED", null, false)}
        onRetry={() => undefined}
      />,
    );
    const note = screen.getByTestId("trace-expired");
    expect(note).toHaveTextContent("Trace expired");
    expect(note).toHaveTextContent("A comparison or an evaluation needs the trace");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("comparing a run whose trace expired shows the words, from the 409", async () => {
    search = `left=${EXPIRED_RUN_ID}&right=${successRun().run.id}`;
    stubApi();
    renderWithQuery(<CompareView base={BASE} />);
    expect(await screen.findByTestId("trace-expired")).toHaveTextContent("Trace expired");
  });

  it("a 500 keeps its own words and its retry", () => {
    render(
      <ErrorState
        error={new ApiRequestError("It broke.", 500, "X", "req_2", true)}
        onRetry={() => undefined}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("It broke.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("analytics say what they leave out (ADR-161)", () => {
  it("shows 'Excludes N runs whose trace has expired' beside cost, tools and latency, only when N is above zero", async () => {
    const withN = (kind: string, n: number) => {
      const body = fixtureAnalytics(kind, new URLSearchParams()) as Record<string, unknown>;
      return Response.json({ ...body, excluded_expired_traces: n });
    };
    stubApi({
      "/analytics/cost": () => withN("cost", 3),
      "/analytics/reliability": () => withN("reliability", 1),
      "/analytics/performance": () => withN("performance", 3),
    });
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByRole("heading", { name: "Slow operations" });
    await waitFor(() => expect(screen.getAllByTestId("expired-traces")).toHaveLength(3));
    const notes = screen.getAllByTestId("expired-traces").map((n) => n.textContent ?? "");
    expect(notes[0]).toContain("Excludes 3 runs whose trace has expired");
    expect(notes[0]).toContain("cost by agent, model and source");
    expect(notes[1]).toContain("Excludes 1 run whose trace has expired");
    expect(notes[2]).toContain("model and tool latencies");
    expect(notes[0]).toContain("Run counts, totals and the cost headline include them");
  });

  it("shows no note when nothing is excluded", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByRole("heading", { name: "Slow operations" });
    expect(screen.queryByTestId("expired-traces")).toBeNull();
  });
});
