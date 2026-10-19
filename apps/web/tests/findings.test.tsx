import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Findings } from "@/components/Findings";
import { RunDetail } from "@/components/RunDetail";
import { RunsTable } from "@/components/RunsTable";
import { fixtureRuns } from "@/fixtures";
import { withFindings } from "@/fixtures/findings";
import { cascadeRun, failingChainRun, loopingRun, successRun } from "@/fixtures/runs";
import type { FindingOut, RunFindingsOut, RunOut } from "@/lib/api/types";
import {
  analysisAgeSeconds,
  findingsCell,
  formatAge,
  stateLine,
  summarizeEvidence,
} from "@/lib/findings";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const looping = withFindings(loopingRun());
const failing = withFindings(failingChainRun());
const cascade = withFindings(cascadeRun());

const reply =
  (body: unknown, status = 200) =>
  () =>
    Response.json(body, { status });
const withState = (
  base: RunFindingsOut,
  state: Partial<RunFindingsOut["state"]>,
  extra: Partial<RunFindingsOut> = {},
): RunFindingsOut => ({ ...base, ...extra, state: { ...base.state, ...state } });

function renderPanel(
  body: RunFindingsOut | (() => Response),
  runActive = false,
  clock?: () => number,
) {
  const onShowEvent = vi.fn();
  stubApi({ "/findings": typeof body === "function" ? body : reply(body) });
  renderWithQuery(
    <Findings
      runId={looping.run.id}
      runActive={runActive}
      summaryProcessing={false}
      onShowEvent={onShowEvent}
      clock={clock}
    />,
  );
  return onShowEvent;
}

describe("Findings panel", () => {
  it("shows loading, then the findings; the health dimensions are not on the page (ADR-166: API only)", async () => {
    renderPanel(looping.findings!);
    expect(screen.getByRole("status")).toHaveTextContent("Loading findings");
    expect(await screen.findAllByTestId("finding")).toHaveLength(1);
    expect(screen.queryByTestId("health")).toBeNull();
    expect(document.querySelector("[data-dimension], .health-disclosure")).toBeNull();
    // no numeric score anywhere
    expect(document.body.textContent).not.toMatch(/\d+\s*\/\s*100/);
  });

  describe("as-of label for a live run (KI-097)", () => {
    afterEach(() => vi.useRealTimers());
    const T0 = Date.parse("2026-10-18T12:00:12Z");
    const live = (computedAt: string | null) =>
      withState(
        looping.findings!,
        { status: "processing", computed_at: computedAt },
        { server_time: "2026-10-18T12:00:12Z" },
      );

    it("reads as of 12 s ago for an injected clock, then keeps counting", async () => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(T0);
      let now = T0;
      renderPanel(live("2026-10-18T12:00:00Z"), true, () => now);
      const line = await screen.findByTestId("findings-state");
      await waitFor(() => expect(line).toHaveTextContent("as of 12 s ago"));
      expect(line).not.toHaveTextContent("Analysing…");
      now = T0 + 5000;
      await waitFor(() => expect(line).toHaveTextContent("as of 17 s ago"), { timeout: 3000 });
    });

    it("keeps listing the earlier findings while the new analysis runs", async () => {
      renderPanel(live("2026-10-18T12:00:00Z"), true);
      expect(await screen.findAllByTestId("finding")).toHaveLength(1);
    });

    it("says Analysing for a run that was never analysed, with no age", async () => {
      renderPanel(live(null), true);
      const line = await screen.findByTestId("findings-state");
      expect(line).toHaveTextContent("Analysing…");
      expect(line).not.toHaveTextContent(/as of/);
    });
  });

  it("labels a heuristic finding Probable in words and a typed fact Observed", async () => {
    renderPanel(failing.findings!);
    const items = await screen.findAllByTestId("finding");
    expect(items).toHaveLength(2);
    const chain = items.find((i) => i.dataset.type === "failure_chain")!;
    const cause = items.find((i) => i.dataset.type === "probable_cause")!;
    expect(within(chain).getByText("Observed")).toBeInTheDocument();
    expect(within(cause).getByText("Probable")).toBeInTheDocument();
    expect(within(cause).getByRole("heading", { level: 3 })).toHaveTextContent(/^Probable: /);
    expect(within(chain).getByRole("heading", { level: 3 })).not.toHaveTextContent(/Probable/);
    // severity is a word and a glyph, most severe first
    expect(items[0]).toHaveAttribute("data-severity", "critical");
    expect(within(items[0]!).getByText("Critical")).toBeInTheDocument();
  });

  it("renders the failure chain as an ordered list of buttons that jump to events", async () => {
    const jump = renderPanel(failing.findings!);
    const user = userEvent.setup();
    const chain = (await screen.findAllByTestId("finding")).find(
      (i) => i.dataset.type === "failure_chain",
    )!;
    const steps = within(chain).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual([
      "mutation · file.modified",
      "retry · retry.attempted",
      "root · shell.command.completed",
      "terminal · run.failed",
    ]);
    await user.click(within(chain).getByRole("button", { name: "root · shell.command.completed" }));
    expect(jump).toHaveBeenCalledWith(failing.events[10]!.event_id);
    await user.click(within(chain).getByRole("button", { name: "Show evidence" }));
    expect(jump).toHaveBeenLastCalledWith(failing.events[10]!.event_id); // start_event_id
  });

  it("shows the cascade's failures, alternates and retries", async () => {
    renderPanel(cascade.findings!);
    const f = await screen.findByTestId("finding");
    expect(f).toHaveTextContent("3 failures in 6 calls, with 2 retries between");
    expect(
      within(f).getAllByRole("button", { name: /tool\.call\.failed · upsert_record · Timeout/ }),
    ).toHaveLength(3);
    expect(within(f).getByLabelText("Pattern")).toHaveTextContent("bulk_import");
  });

  it("says No findings for an analysed run without any", async () => {
    const ok = withFindings(successRun());
    renderPanel(ok.findings!);
    expect(await screen.findByTestId("findings-empty")).toHaveTextContent("Nothing wrong found.");
    expect(screen.getByTestId("findings-state")).toHaveTextContent("Analysis is current.");
    expect(screen.queryByTestId("finding")).toBeNull();
  });

  it.each([
    ["none", {}, "Not analysed yet."],
    ["processing", { computed_at: null }, "Analysing…"],
    [
      "outdated",
      { detectors_version: 0 },
      "Detector rules changed since this analysis over 40 events; the findings may be outdated.",
    ],
    [
      "failed",
      {},
      "The latest analysis failed and needs an operator. Showing the last successful analysis",
    ],
  ] as const)("renders the %s state honestly", async (status, extra, text) => {
    renderPanel(withState(looping.findings!, { status, ...extra }));
    expect(await screen.findByTestId("findings-state")).toHaveTextContent(text);
    expect(document.querySelector('[data-state="' + status + '"]')).not.toBeNull();
  });

  it("does not present a failed analysis as healthy: it says the analysis failed and lists the last findings", async () => {
    renderPanel(withState(looping.findings!, { status: "failed" }));
    expect(await screen.findByTestId("findings-state")).toHaveTextContent(
      "The latest analysis failed and needs an operator.",
    );
    // the previous findings are still listed, and nothing on the panel calls the run healthy or OK
    expect(screen.getAllByTestId("finding")).toHaveLength(1);
    expect(screen.queryByTestId("health")).toBeNull();
    expect(document.body.textContent).not.toMatch(/healthy|\bOK\b/);
  });

  it("says when only the first events of a huge run were analysed", async () => {
    renderPanel(withState(looping.findings!, { truncated: true, event_count_seen: 50_000 }));
    expect(await screen.findByTestId("findings-state")).toHaveTextContent(
      "Analysed the first 50,000 events only; the run is longer.",
    );
  });

  it("warns that severity may change while the run is active", async () => {
    renderPanel(looping.findings!, true);
    expect(await screen.findByTestId("findings-state")).toHaveTextContent(
      "Severity may change until the run ends.",
    );
  });

  it("shows an error state with retry when the findings cannot be loaded", async () => {
    renderPanel(reply({ error: { code: "UNAVAILABLE", message: "down", retryable: true } }, 503));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("down");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("renders hostile strings in evidence as text, never as HTML", async () => {
    const evil = '<img src=x onerror="window.__pwned=1">';
    const hostile: FindingOut = {
      ...looping.findings!.items[0]!,
      title: `Probable: ${evil}`,
      message: `<script>window.__pwned=1</script> ${evil}`,
      evidence: {
        agent_id: evil,
        pattern: [evil, "<b>bold</b>"],
        repetitions: 6,
        thresholds: { [evil]: 3 },
      },
    };
    renderPanel({ ...looping.findings!, items: [hostile] });
    const f = await screen.findByTestId("finding");
    expect(f.querySelector("img, script, b")).toBeNull();
    expect(f).toHaveTextContent(evil);
    expect(f).toHaveTextContent("<b>bold</b>");
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("survives malformed evidence", async () => {
    const odd: FindingOut = {
      ...looping.findings!.items[0]!,
      evidence: { pattern: "not a list", repetitions: "many", thresholds: [1, 2], failures: 7 },
    };
    renderPanel({ ...looping.findings!, items: [odd] });
    expect(await screen.findByTestId("finding")).toBeInTheDocument();
  });
});

describe("RunDetail with findings", () => {
  it("Show evidence, from the Findings tab, shows the event in the timeline and opens the drawer", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={looping.run.id} base={BASE} live={false} />);
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const finding = await screen.findByTestId("finding");
    expect(finding).toHaveTextContent("Probable: the agent may be looping");
    await user.click(within(finding).getByRole("button", { name: "Show evidence" }));
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("tool.call.completed");
    expect(await screen.findByRole("option", { selected: true })).toHaveTextContent(
      "tool.call.completed",
    );
  });

  it("clears a filter that hides the cited event", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={looping.run.id} base={BASE} live={false} />);
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    await user.click(screen.getByLabelText("Errors only"));
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const finding = await screen.findByTestId("finding");
    await user.click(within(finding).getByRole("button", { name: "Show evidence" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Errors only")).not.toBeChecked();
  });

  it("opens the drawer from the event route when the cited event is not loaded", async () => {
    const calls = stubApi({
      // the timeline only ever pages in the first three events
      "/events": () => {
        const items = looping.events.slice(0, 3).map((e) => ({ ...e, payload: null }));
        return Response.json({ items, next_cursor: null, ordering_mode: "sequence" });
      },
    });
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={looping.run.id} base={BASE} live={false} />);
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("3 of 3"));
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const finding = await screen.findByTestId("finding");
    await user.click(within(finding).getByRole("button", { name: "Show evidence" }));
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("tool.call.completed");
    expect(calls.some((c) => c.includes(`/events/${looping.events[3]!.event_id}`))).toBe(true);
  });

  it("prefers the loaded timeline event over the event route", async () => {
    stubApi({
      [`/events/${looping.events[3]!.event_id}`]: reply(
        { error: { code: "EVENT_NOT_FOUND", message: "Event not found.", retryable: false } },
        404,
      ),
    });
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={looping.run.id} base={BASE} live={false} />);
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const finding = await screen.findByTestId("finding");
    await user.click(within(finding).getByRole("button", { name: "Show evidence" }));
    // loaded events still win: the event is in the timeline, so no notice and a drawer
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("event-notice")).toBeNull();
  });

  it("shows a notice, not a crash, for an event that is neither loaded nor retrievable", async () => {
    const missing = {
      ...failing.findings!.items[0]!,
      start_event_id: "evt_00000000000000000000009999",
    };
    stubApi({
      [`/runs/${failing.run.id}/findings`]: reply({ ...failing.findings!, items: [missing] }),
    });
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failing.run.id} base={BASE} live={false} />);
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    const finding = await screen.findByTestId("finding");
    await user.click(within(finding).getAllByRole("button", { name: "Show evidence" })[0]!);
    const notice = await screen.findByTestId("event-notice");
    expect(notice).toHaveTextContent("could not be loaded");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(within(notice).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("event-notice")).toBeNull();
  });

  it("the first-error Go button still works through the shared jump", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunDetail runId={failing.run.id} base={BASE} live={false} />);
    await user.click(await screen.findByRole("button", { name: "Go to first error" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("runs list findings column", () => {
  it("shows count and worst severity as text, and a dash with plain words otherwise", async () => {
    const runs = fixtureRuns().map((f) => f.run);
    renderWithQuery(<RunsTable runs={runs} base={BASE} caption="Runs" />);
    expect(screen.getByRole("columnheader", { name: "Findings" })).toBeInTheDocument();
    const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr")!;
    expect(
      within(rowOf("Resolve flaky docs search")).getByTestId("findings-cell"),
    ).toHaveTextContent("1 · info");
    expect(within(rowOf("Fix flaky checkout test")).getByTestId("findings-cell")).toHaveTextContent(
      "2 · critical",
    );
    // One glyph, one meaning: run findings carry the severity glyph of the Findings tab, never the Security shield.
    const crit = within(rowOf("Fix flaky checkout test")).getByTestId("findings-cell");
    expect(crit.querySelector("svg.sec-shield")).toBeNull();
    expect(within(crit).getByTestId("run-findings")).toHaveTextContent("✕");
    const ok = within(rowOf("Fix login session expiry")).getByTestId("findings-cell");
    expect(ok).toHaveTextContent("No findings");
    expect(ok.querySelector('[aria-hidden="true"]')).toHaveTextContent("—");
    const waiting = within(rowOf("Deploy to staging")).getByTestId("findings-cell");
    expect(waiting).toHaveTextContent("Not analysed yet");
  });
});

describe("runs list findings column: security is its own mark", () => {
  const withSecurity = (sec: RunOut["security_findings"], badge: RunOut["findings"]) => {
    const run = { ...fixtureRuns()[0]!.run, findings: badge, security_findings: sec };
    renderWithQuery(<RunsTable runs={[run]} base={BASE} caption="Runs" />);
    return screen.getByTestId("findings-cell");
  };

  it("shows run findings and security findings as two distinct, named marks", () => {
    const cell = withSecurity(
      { count: 2, max_severity: "high", state: "current" },
      { count: 3, max_severity: "critical", state: "current" },
    );
    const run = within(cell).getByTestId("run-findings");
    expect(run).toHaveTextContent("3 · critical");
    expect(run.querySelector("svg")).toBeNull();
    const sec = within(cell).getByTestId("security-findings");
    expect(sec.querySelector("svg.sec-shield")).not.toBeNull();
    expect(sec).toHaveTextContent("2 security findings");
    expect(cell.querySelectorAll("svg.sec-shield")).toHaveLength(1);
  });

  it("never draws the shield for run findings alone", () => {
    const only = withSecurity(
      { count: 0, max_severity: null, state: "current" },
      { count: 1, max_severity: "warning", state: "current" },
    );
    expect(only.querySelector("svg.sec-shield")).toBeNull();
    expect(within(only).queryByTestId("security-findings")).toBeNull();
  });

  it("security findings alone still show the shield with an accessible name", () => {
    const cell = withSecurity(
      { count: 1, max_severity: "critical", state: "current" },
      { count: 0, max_severity: null, state: "current" },
    );
    expect(within(cell).queryByTestId("run-findings")).toBeNull();
    expect(within(cell).getByTestId("security-findings")).toHaveTextContent("1 security finding");
    expect(cell.querySelector("svg.sec-shield")).not.toBeNull();
  });
});

describe("findings helpers", () => {
  it("findingsCell covers null, none, empty, current and outdated", () => {
    expect(findingsCell(null).title).toBe("Not analysed yet");
    expect(findingsCell({ count: 0, max_severity: null, state: "none" }).visible).toBe("");
    expect(findingsCell({ count: 0, max_severity: null, state: "current" }).title).toBe(
      "No findings",
    );
    expect(findingsCell({ count: 3, max_severity: "critical", state: "current" }).visible).toBe(
      "3 · critical",
    );
    const stale = findingsCell({ count: 1, max_severity: "info", state: "outdated" });
    expect(stale.visible).toBe("1 · info (outdated)");
    expect(stale.title).toContain("may be outdated");
  });

  it("formats an analysis age coarsely and measures it on the server's clock", () => {
    expect(formatAge(12)).toBe("12 s ago");
    expect(formatAge(0.4)).toBe("0 s ago");
    expect(formatAge(59.6)).toBe("1 min ago");
    expect(formatAge(190)).toBe("3 min ago");
    expect(formatAge(7300)).toBe("2 h ago");
    const state = { computed_at: "2026-10-18T12:00:00Z" };
    expect(analysisAgeSeconds(state, "2026-10-18T12:00:12Z", 0)).toBe(12);
    expect(analysisAgeSeconds(state, "2026-10-18T12:00:12Z", 5000)).toBe(17);
    expect(analysisAgeSeconds(state, "2026-10-18T11:59:00Z", 0)).toBe(0); // never negative
    expect(analysisAgeSeconds({ computed_at: null }, "2026-10-18T12:00:12Z", 0)).toBeNull();
    expect(analysisAgeSeconds(state, undefined, 0)).toBeNull();
    expect(analysisAgeSeconds({ computed_at: "garbage" }, "2026-10-18T12:00:12Z", 0)).toBeNull();
  });

  it("a queued analysis shows the last one with its age; only a never-analysed run says Analysing", () => {
    const base = looping.findings!.state;
    const queued = { ...base, status: "processing" as const };
    expect(stateLine(queued, true, 12).text).toBe(
      "Showing the last analysis, as of 12 s ago over 40 events. Newer events are being analysed.",
    );
    expect(stateLine({ ...queued, computed_at: null }, true, null).text).toBe("Analysing…");
  });

  it("stateLine never calls a stale or failed analysis current", () => {
    const base = looping.findings!.state;
    expect(stateLine({ ...base, status: "outdated" }, false).text).toMatch(/may be outdated/);
    expect(stateLine({ ...base, status: "failed" }, false).tone).toBe("bad");
    expect(stateLine({ ...base, status: "processing", computed_at: null }, false).text).toBe(
      "Analysing…",
    );
    expect(stateLine({ ...base, status: "current" }, true).text).toMatch(/events so far/);
  });

  it("summarizeEvidence reads typed evidence and ignores anything malformed", () => {
    const s = summarizeEvidence(looping.findings!.items[0]!);
    expect(s.chips).toEqual(["tool:search_docs", "tool:read_file", "llm"]);
    expect(s.facts).toContainEqual(["Repetitions", "6"]);
    expect(s.thresholds.length).toBeGreaterThan(0);
    const bad = summarizeEvidence({
      finding_type: "failure_chain",
      evidence: { chain: [1, null, { event_id: 5 }] },
    });
    expect(bad.steps).toEqual([]);
  });
});
