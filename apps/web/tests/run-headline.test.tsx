import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RunHeadline } from "@/components/RunHeadline";
import type { EventOut, RunOut } from "@/lib/api/types";
import { withholdEvent, API_WITHHELD } from "@/fixtures/withhold";
import { approvalRun, blockedRun, failureRetryRun, runningRun, successRun } from "@/fixtures/runs";
import { firstError, firstErrorFor } from "@/lib/timeline";
import {
  HEADLINE_KINDS,
  headlineKind,
  headlineModel,
  type HeadlineKind,
  type HeadlineModel,
} from "@/lib/runHeadline";
import type { FixtureRun } from "@/fixtures/builder";

const t0Of = (events: readonly EventOut[]) =>
  events.reduce((m, e) => Math.min(m, Date.parse(e.occurred_at)), Infinity);

const model = (
  f: FixtureRun,
  over: Partial<RunOut> = {},
  opts: { events?: EventOut[]; complete?: boolean; now?: number } = {},
): HeadlineModel => {
  const events = opts.events ?? f.events;
  return headlineModel({
    run: { ...f.run, ...over },
    events,
    t0: t0Of(events),
    eventsComplete: opts.complete ?? true,
    now: opts.now,
  });
};

/** A fixture run dressed as each of the six states. */
const FOR_KIND: Record<HeadlineKind, () => HeadlineModel> = {
  failed: () => model(failureRetryRun()),
  recovered: () => model(failureRetryRun(), { status: "SUCCESS" }),
  success: () => model(successRun()),
  running: () => model(runningRun()),
  waiting: () => model(approvalRun()),
  cancelled: () => model(successRun(), { status: "CANCELLED" }),
};

describe("firstErrorFor() agrees with the server's rule (shared cases, ADR-112)", () => {
  const file = path.join(
    __dirname,
    "../../../packages/event-schema/tests/data/first-error-cases.json",
  );
  const { cases } = JSON.parse(readFileSync(file, "utf8")) as {
    cases: {
      name: string;
      run_status?: RunOut["status"];
      events: Partial<EventOut>[];
      expected: string | null;
    }[];
  };
  it("reads the shared cases at all, blocked ones included", () => {
    expect(cases.length).toBeGreaterThanOrEqual(12);
    expect(cases.filter((c) => c.run_status === "BLOCKED").length).toBeGreaterThanOrEqual(3);
  });
  for (const c of cases)
    it(c.name, () => {
      const events = c.events.map((e) => ({ attributes: {}, ...e }) as EventOut);
      expect(firstErrorFor(c.run_status ?? "RUNNING", events)?.event_id ?? null).toBe(c.expected);
      // an event-only rule is unchanged: a blocking event never makes firstError() answer
      if (c.run_status !== "BLOCKED") expect(firstError(events)?.event_id ?? null).toBe(c.expected);
    });
});

describe("headlineModel: the six states", () => {
  it("failed: step, offset, tool and error type; retries, time and cost after, total", () => {
    const m = model(failureRetryRun());
    expect(m.kind).toBe("failed");
    expect(m.lead).toMatch(/^Failed at step \d+ after 6\.2 s: run_migration, ConnectionTimeout\.$/);
    // 2 retries, then a 1.5 s model call: cost after is that call alone
    expect(m.detail).toMatch(/^2 retries · [\d.]+ s and \$0\.0167 spent after · \$0\.0356 total$/);
    expect(m.failure?.eventId).toBeTruthy();
    expect(m.pending).toBe(false);
  });
  it("timed out and blocked use their own word", () => {
    expect(model(failureRetryRun(), { status: "TIMED_OUT" }).lead).toMatch(/^Timed out at step/);
    expect(model(failureRetryRun(), { status: "BLOCKED" }).lead).toMatch(/^Blocked at step/);
  });
  it("blocked: the blocking event is the first error, named by its kind", () => {
    const f = blockedRun();
    expect(f.run.status).toBe("BLOCKED");
    expect(f.run.summary["error_count"]).toBe(0);
    const m = model(f);
    expect(m.kind).toBe("failed");
    expect(m.lead).toMatch(/^Blocked at step \d+ after [\d.]+ s: policy action blocked\.$/);
    expect(m.failure?.eventId).toBe(
      f.events.find((e) => e.event_type === "policy.action.blocked")!.event_id,
    );
    expect(m.detail).toMatch(/^No retries/);
    // the loaded events alone find it too (an older summary, or the stored one still catching up)
    const loaded = model(f, { first_error: null, summary_version: 5 });
    expect(loaded.failure?.eventId).toBe(m.failure?.eventId);
  });
  it("blocked with only the blocked completion names the run's end", () => {
    const f = blockedRun();
    const events = f.events.filter((e) => e.event_type !== "policy.action.blocked");
    const m = model(f, { first_error: null }, { events });
    expect(m.failure?.eventId).toBe(events[events.length - 1]!.event_id);
    expect(m.lead).toMatch(/^Blocked at step \d+ after [\d.]+ s: the run's end\.$/);
  });
  it("a run that ends in a timeout or failure at its last event says 'the run's end', not the event type", () => {
    const f = failureRetryRun();
    const end = f.events[f.events.length - 1]!;
    const events = f.events.filter((e) => e.status !== "error" || e === end);
    expect(end.event_type).toBe("run.failed");
    const m = model(f, { status: "TIMED_OUT", first_error: null }, { events });
    expect(m.lead).toMatch(/: the run's end\.$/);
    expect(m.lead).not.toMatch(/run\.failed|, error/);
    const timedOut: EventOut = { ...end, event_type: "run.completed", status: "timeout" };
    const m2 = model(
      f,
      { status: "TIMED_OUT", first_error: null },
      { events: [...events.slice(0, -1), timedOut] },
    );
    expect(m2.lead).toMatch(/^Timed out at step \d+ after [\d.]+ s: the run's end\.$/);
  });
  it("failed without any error event says how long it ran, and offers no jump", () => {
    const f = successRun();
    const m = model(f, { status: "FAILED" });
    expect(m.lead).toMatch(/^Failed after 11 s\.$/);
    expect(m.failure).toBeNull();
  });
  it("uses the server's stored facts when present and says nothing about where they came from", () => {
    const f = failureRetryRun();
    const err = firstError(f.events)!;
    const m = model(f, {
      first_error: {
        event_id: err.event_id,
        sequence: 77,
        retries_after: 9,
        seconds_after: 12.4,
        cost_after_usd: 1.5,
        cost_after_unpriced_calls: 2,
      },
    });
    expect(m.lead).toContain("step 77");
    expect(m.detail).toBe(
      "9 retries · 12 s and $1.50 spent after (2 calls unpriced) · $0.0356 total",
    );
    expect(`${m.lead} ${m.detail}`).not.toMatch(/server|computed|derived/i);
  });
  it("an older run (no stored facts) falls back to the loaded events; with events still paging it waits", () => {
    const f = failureRetryRun();
    const fromEvents = model(f, { first_error: null });
    expect(fromEvents.detail).toMatch(/^2 retries · /);
    const paging = model(f, { first_error: null }, { complete: false });
    expect(paging.pending).toBe(true);
    expect(paging.detail).toBe("");
    expect(paging.failure).not.toBeNull();
  });
  it("a summary that is still catching up is not trusted over the loaded events", () => {
    const f = failureRetryRun();
    const err = firstError(f.events)!;
    const m = model(f, {
      summary_state: "processing",
      first_error: {
        event_id: "stale",
        sequence: 1,
        retries_after: 99,
        seconds_after: 1,
        cost_after_usd: 9,
        cost_after_unpriced_calls: 0,
      },
    });
    expect(m.failure?.eventId).toBe(err.event_id);
    expect(m.detail).toMatch(/^2 retries/);
  });
  it("running: step, how long since the last event, cost so far, a live dot", () => {
    const f = runningRun();
    const last = f.events[f.events.length - 1]!;
    const m = model(f, {}, { now: Date.parse(last.occurred_at) + 3_000 });
    expect(m.kind).toBe("running");
    expect(m.live).toBe(true);
    expect(m.lead).toMatch(/^Running at step \d+, last event (3s ago|just now)\.$/);
    expect(m.detail).toMatch(/so far/);
  });
  it("waiting for approval: what it waits on, for how long, the step", () => {
    const f = approvalRun();
    const ask = f.events.find((e) => e.event_type === "approval.requested")!;
    const m = model(f, {}, { now: Date.parse(ask.occurred_at) + 4 * 60_000 });
    expect(m.kind).toBe("waiting");
    expect(m.lead).toMatch(/^Waiting for approval at step \d+\.$/);
    expect(m.detail).toMatch(/waiting 4m 00s/);
  });
  it("recovered: succeeded after N retries, the error it recovered from, what the recovery cost", () => {
    const m = model(failureRetryRun(), { status: "SUCCESS" });
    expect(m.kind).toBe("recovered");
    expect(m.lead).toBe("Succeeded after 2 retries.");
    expect(m.detail).toMatch(
      /^Recovered from run_migration, ConnectionTimeout at step \d+ · \$0\.0167 spent on the recovery · \$0\.0356 total$/,
    );
  });
  it("clean success: calm, duration and cost; files and tests when known", () => {
    const m = model(successRun());
    expect(m.kind).toBe("success");
    expect(m.lead).toBe("Completed in 11 s for $0.0253.");
    expect(m.detail).toBe("1 file changed");
    expect(m.failure).toBeNull();
    const f = successRun();
    const last = f.events[f.events.length - 1]!;
    const withTests: EventOut = {
      ...last,
      event_id: "evt_tests",
      attributes: { "test.total": 12, "test.failed": 0 },
    };
    expect(model(f, {}, { events: [...f.events, withTests] }).detail).toBe(
      "1 file changed · tests pass",
    );
    const failing = { ...withTests, attributes: { "test.total": 12, "test.failed": 2 } };
    expect(model(f, {}, { events: [...f.events, failing] }).detail).toBe(
      "1 file changed · 2 tests failed",
    );
  });
  it("cancelled: at which step, after how long, and who or what stopped it", () => {
    const m = model(successRun(), { status: "CANCELLED" });
    expect(m.lead).toMatch(/^Cancelled at step \d+ after 11 s\.$/);
    expect(m.detail).toBe("$0.0253 total");
    const f = approvalRun();
    const denied: EventOut = {
      ...f.events[f.events.length - 1]!,
      event_id: "evt_denied",
      event_type: "approval.denied",
      attributes: { "approval.action": "git push" },
    };
    const m2 = model(f, { status: "CANCELLED" }, { events: [...f.events, denied] });
    expect(m2.detail).toMatch(/^Cancelled after an approval was denied · /);
  });
  it("covers every status", () => {
    const run = successRun().run;
    const kinds = (
      [
        "QUEUED",
        "RUNNING",
        "WAITING",
        "WAITING_FOR_APPROVAL",
        "SUCCESS",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
        "BLOCKED",
      ] as const
    ).map((status) => headlineKind({ ...run, status }, false));
    expect(new Set(kinds)).toEqual(new Set(HEADLINE_KINDS.filter((k) => k !== "recovered")));
  });
});

describe("headline sentences (design pass 2): one sentence, full stop, no parentheses or middle dots", () => {
  const CASES: [string, () => HeadlineModel][] = [
    ["failed", () => model(failureRetryRun())],
    ["timed out", () => model(failureRetryRun(), { status: "TIMED_OUT" })],
    ["blocked", () => model(blockedRun())],
    ["failed, first error unknown", () => model(successRun(), { status: "FAILED" })],
    ["recovered", () => model(failureRetryRun(), { status: "SUCCESS" })],
    ["success", () => model(successRun())],
    ["running", () => model(runningRun())],
    ["waiting for approval", () => model(approvalRun())],
    ["cancelled", () => model(successRun(), { status: "CANCELLED" })],
  ];
  for (const [name, make] of CASES)
    it(`${name}: a sentence`, () => {
      const { lead } = make();
      expect(lead).toMatch(/^[A-Z][^·()]*\.$/);
    });
  it("each status opens with its own word", () => {
    expect(model(failureRetryRun()).lead).toMatch(/^Failed at step/);
    expect(model(failureRetryRun(), { status: "TIMED_OUT" }).lead).toMatch(/^Timed out at step/);
    expect(model(blockedRun()).lead).toMatch(/^Blocked at step/);
    expect(model(runningRun()).lead).toMatch(/^Running/);
    expect(model(approvalRun()).lead).toMatch(/^Waiting for approval/);
    expect(model(successRun(), { status: "CANCELLED" }).lead).toMatch(/^Cancelled/);
    expect(model(successRun()).lead).toMatch(/^Completed in/);
  });
});

describe("headline states x variants", () => {
  for (const kind of HEADLINE_KINDS) {
    describe(kind, () => {
      it("ready: the lead and the mono line, with a Go control only when there is a failure", async () => {
        const m = FOR_KIND[kind]();
        const onGo = vi.fn();
        render(<RunHeadline kind={kind} variant="ready" model={m} onGo={onGo} />);
        const lead = screen.getByTestId("headline");
        expect(lead).toHaveTextContent(m.lead);
        expect(screen.getByTestId("headline-detail")).toHaveTextContent(m.detail);
        const go = screen.queryByRole("button", { name: "Go to first error" });
        if (m.failure) {
          expect(go).toHaveTextContent("Go to first error");
          await userEvent.click(go!);
          expect(onGo).toHaveBeenCalledTimes(1);
        } else {
          expect(go).toBeNull();
        }
      });
      it("skeleton: the same frame, no words, announced once", () => {
        render(<RunHeadline kind={kind} variant="skeleton" />);
        expect(screen.getByRole("status")).toHaveTextContent("Loading");
        expect(screen.queryByTestId("headline")).toBeNull();
        expect(document.querySelector(".hl")).toHaveAttribute("aria-busy", "true");
        expect(document.querySelectorAll(".hl-lead").length).toBe(1);
        expect(document.querySelectorAll(".hl-detail").length).toBe(1);
      });
      it("error: plain text and a retry", async () => {
        const onRetry = vi.fn();
        render(<RunHeadline kind={kind} variant="error" onRetry={onRetry} />);
        const alert = screen.getByRole("alert");
        expect(alert).toHaveTextContent(/^Could not load/);
        await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
        expect(onRetry).toHaveBeenCalledTimes(1);
      });
      it("withheld: a viewer's headline is the same facts and never shows content", () => {
        const f = {
          failed: failureRetryRun,
          recovered: failureRetryRun,
          success: successRun,
          running: runningRun,
          waiting: approvalRun,
          cancelled: successRun,
        }[kind]();
        const over: Partial<RunOut> =
          kind === "recovered"
            ? { status: "SUCCESS" }
            : kind === "cancelled"
              ? { status: "CANCELLED" }
              : {};
        const viewerEvents = f.events.map((e) => withholdEvent(e, false));
        const owner = model(f, over);
        const viewer = model(f, over, { events: viewerEvents });
        expect(viewer.lead).toBe(owner.lead);
        expect(viewer.detail).toBe(owner.detail);
        render(<RunHeadline kind={kind} variant="ready" model={viewer} onGo={() => {}} />);
        const text = document.body.textContent ?? "";
        expect(text).not.toContain(API_WITHHELD);
        for (const e of f.events)
          for (const k of ["file.path", "shell.command", "shell.cwd"]) {
            const v = e.attributes[k];
            if (typeof v === "string") expect(text).not.toContain(v);
          }
      });
    });
  }

  it("withheld details on the failing event are named as withheld, not shown", () => {
    const f = failureRetryRun();
    const err = firstError(f.events)!;
    const events = f.events.map((e) =>
      e.event_id === err.event_id ? { ...e, withheld_attributes: ["shell.command"] } : e,
    );
    const m = model(f, {}, { events });
    expect(m.failure?.withheld).toBe(true);
    render(<RunHeadline kind="failed" variant="ready" model={m} onGo={() => {}} />);
    expect(screen.getByTestId("headline-detail")).toHaveTextContent(
      "details withheld for your role",
    );
  });

  it("no attribute outside the reviewed metadata ever reaches the headline, for any role (review P2-1)", () => {
    const SENTINEL = "SENTINEL-do-not-print";
    // Every free-text attribute the registry does not classify as reviewed metadata, plus the content ones.
    const UNREVIEWED = [
      "approval.action",
      "error.summary",
      "file.path",
      "shell.command",
      "shell.cwd",
      "git.branch",
      "git.push_target",
      "run.note",
    ];
    const dirty = (f: FixtureRun): FixtureRun => ({
      ...f,
      events: f.events.map((e) => ({
        ...e,
        attributes: {
          ...e.attributes,
          ...Object.fromEntries(UNREVIEWED.map((k) => [k, SENTINEL])),
        },
      })),
    });
    const cases: [string, FixtureRun, Partial<RunOut>][] = [
      ["failed", failureRetryRun(), {}],
      ["blocked", blockedRun(), {}],
      ["timed out", failureRetryRun(), { status: "TIMED_OUT" }],
      ["recovered", failureRetryRun(), { status: "SUCCESS" }],
      ["success", successRun(), {}],
      ["running", runningRun(), {}],
      ["awaiting approval", approvalRun(), {}],
      ["waiting", runningRun(), { status: "WAITING" }],
      ["cancelled", successRun(), { status: "CANCELLED" }],
    ];
    for (const [name, base, over] of cases) {
      const f = dirty(base);
      const denied: EventOut = {
        ...f.events[f.events.length - 1]!,
        event_id: "evt_denied",
        event_type: "approval.denied",
      };
      const withDenied = name === "cancelled" ? [...f.events, denied] : f.events;
      for (const role of ["owner", "viewer"] as const) {
        const events =
          role === "viewer" ? withDenied.map((e) => withholdEvent(e, false)) : withDenied;
        const m = model(f, over, { events });
        expect(`${m.lead} | ${m.detail}`, `${name} / ${role}`).not.toContain(SENTINEL);
        expect(`${m.lead} | ${m.detail}`, `${name} / ${role}`).not.toContain(API_WITHHELD);
      }
    }
  });

  it("an awaiting-approval or cancelled headline names the kind, not what the approval was for", () => {
    const f = approvalRun();
    expect(model(f).lead).toBe(
      `Waiting for approval at step ${f.events[f.events.length - 1]!.sequence}.`,
    );
    const denied: EventOut = {
      ...f.events[f.events.length - 1]!,
      event_id: "evt_denied",
      event_type: "approval.denied",
      attributes: { "approval.action": "git push" },
    };
    const m = model(f, { status: "CANCELLED" }, { events: [...f.events, denied] });
    expect(m.detail).toMatch(/^Cancelled after an approval was denied · /);
  });

  it("a pending detail is a skeleton bar, not a zero", () => {
    const m = model(failureRetryRun(), { first_error: null }, { complete: false });
    render(<RunHeadline kind="failed" variant="ready" model={m} onGo={() => {}} />);
    expect(screen.getByTestId("headline-detail")).toHaveAttribute("data-pending");
    expect(screen.getByTestId("headline-detail")).not.toHaveTextContent("0");
  });
});
