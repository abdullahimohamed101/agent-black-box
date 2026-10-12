import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { FilterMenu } from "@/components/FilterMenu";
import { Timeline } from "@/components/Timeline";
import { Waterfall } from "@/components/Waterfall";
import { blockedRun, failureRetryRun, successRun } from "@/fixtures/runs";
import type { EventOut } from "@/lib/api/types";
import { deriveSpans, SpanIndex } from "@/lib/spans";
import {
  CLASS_ORDER,
  NO_FILTERS,
  buildRows,
  faultRowIndex,
  filterEvents,
  firstError,
  firstErrorFor,
} from "@/lib/timeline";
import type { EventClass } from "@/lib/timeline";
import { BASE, tool } from "./spanEvents";

const t0Of = (events: readonly EventOut[]) =>
  events.reduce((m, e) => Math.min(m, Date.parse(e.occurred_at)), Infinity);

function timeline(
  events: EventOut[],
  collapsed: ReadonlySet<string> = new Set(),
  step: number | null = 7,
) {
  const err = firstError(events);
  return render(
    <Timeline
      rows={buildRows(events, collapsed)}
      t0={t0Of(events)}
      selectedKey={null}
      firstErrorId={err?.event_id ?? null}
      faultStep={step}
      onSelect={() => {}}
      onActivate={() => {}}
      onEscape={() => {}}
      onJumpToError={() => {}}
    />,
  );
}

describe("the timeline's fault line", () => {
  it("draws one line, labelled with the step, on the first-error row; rows below carry the tint, rows above do not", () => {
    timeline(failureRetryRun().events);
    const lines = document.querySelectorAll(".tl-fault");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent("first error · step 7");
    expect(lines[0]).toHaveAttribute("aria-hidden", "true");
    const rows = screen.getAllByRole("option");
    // Expanded, the line sits on the error event's own row. The group header and the "started" row above it happened
    // before the error: they are neither tinted as "after the fault" nor under the line (review P2-3).
    const at = rows.findIndex((r) => r.hasAttribute("data-first-error"));
    expect(at).toBeGreaterThan(1);
    rows.forEach((r, i) => {
      expect(r.hasAttribute("data-after-fault"), `row ${i}`).toBe(i > at);
    });
    // the line sits on the top edge of that row (36px rows)
    expect((lines[0] as HTMLElement).style.top).toBe(`${at * 36}px`);
  });

  it("anchors on the group when the first error is inside a collapsed group, and tints what follows", () => {
    const events = failureRetryRun().events;
    const rowsExpanded = buildRows(events, new Set());
    const group = rowsExpanded.find((r) => r.kind === "group" && r.hasError)!;
    timeline(events, new Set([group.key]));
    const rows = screen.getAllByRole("option");
    const at = rows.findIndex((r) => r.getAttribute("aria-label")?.includes("contains an error"));
    expect((document.querySelector(".tl-fault") as HTMLElement).style.top).toBe(`${at * 36}px`);
    expect(rows[at]).not.toHaveAttribute("data-after-fault");
    expect(rows[at + 1]).toHaveAttribute("data-after-fault");
  });

  it("a blocked run's line sits on the blocking event, which is not an error row", () => {
    const f = blockedRun();
    const at = firstErrorFor(f.run.status, f.events)!;
    expect(at.event_type).toBe("policy.action.blocked");
    const err = firstError(f.events);
    expect(err).toBeUndefined();
    render(
      <Timeline
        rows={buildRows(f.events, new Set())}
        t0={t0Of(f.events)}
        selectedKey={null}
        firstErrorId={at.event_id}
        faultStep={at.sequence}
        onSelect={() => {}}
        onActivate={() => {}}
        onEscape={() => {}}
        onJumpToError={() => {}}
      />,
    );
    const rows = screen.getAllByRole("option");
    const i = rows.findIndex((r) => r.hasAttribute("data-first-error"));
    expect(i).toBeGreaterThan(0);
    expect(rows[i]).not.toHaveAttribute("data-error");
    expect(document.querySelectorAll(".tl-fault")).toHaveLength(1);
    expect((document.querySelector(".tl-fault") as HTMLElement).style.top).toBe(`${i * 36}px`);
    rows.forEach((r, k) => expect(r.hasAttribute("data-after-fault")).toBe(k > i));
  });

  it("a collapsed group stands for a first error that is inside it, error event or not", () => {
    const events = failureRetryRun().events;
    const group = buildRows(events, new Set()).find((r) => r.kind === "group" && r.hasError)!;
    const collapsed = buildRows(events, new Set([group.key]));
    const startedId = events.find((e) => e.event_type === "tool.call.started")!.event_id;
    // the started event is a member of the first tool group: collapsed, the group's row stands for it
    const g = collapsed.find((r) => r.kind === "group" && r.eventIds.includes(startedId))!;
    expect(faultRowIndex(collapsed, startedId)).toBe(collapsed.indexOf(g));
    // expanded, the header is never the anchor
    expect(faultRowIndex(buildRows(events, new Set()), startedId)).toBeGreaterThan(
      buildRows(events, new Set()).findIndex((r) => r.kind === "group"),
    );
  });

  it("a clean run has no line, no tint and a silent status column", () => {
    timeline(successRun().events);
    expect(document.querySelector(".tl-fault")).toBeNull();
    expect(document.querySelector("[data-after-fault]")).toBeNull();
    // successes keep their status for assistive tech, but nothing is drawn: it is visually hidden text
    const status = [...document.querySelectorAll(".tl-status")].filter((s) => s.textContent);
    expect(status.length).toBeGreaterThan(0);
    for (const s of status) expect(s.querySelector(".sr-only")).not.toBeNull();
  });

  it("failures keep their status text in view; successes do not draw one", () => {
    timeline(failureRetryRun().events);
    const drawn = [...document.querySelectorAll(".tl-status")]
      .filter((s) => s.textContent && !s.querySelector(".sr-only"))
      .map((s) => s.textContent);
    expect(drawn.length).toBeGreaterThan(0);
    for (const t of drawn) expect(t).toMatch(/error|timeout|failed/);
  });

  it("the first-error row still says so in words for assistive technology", () => {
    timeline(failureRetryRun().events);
    const row = document.querySelector("[data-first-error]") as HTMLElement;
    expect(within(row).getByText(/First error/)).toBeInTheDocument();
  });

  it("no line when a filter hides the first error", () => {
    const events = failureRetryRun().events;
    const hidden = filterEvents(events, { ...NO_FILTERS, classes: new Set(["llm"]) });
    expect(faultRowIndex(buildRows(hidden, new Set()), firstError(events)!.event_id)).toBe(-1);
    expect(faultRowIndex(buildRows(events, new Set()), null)).toBe(-1);
  });

  it("the line is on the first row's top edge with its label hanging below", () => {
    const events = failureRetryRun().events.filter((e) => e.status === "error");
    timeline(events, new Set(), 1);
    expect(document.querySelector(".tl-fault")).toHaveAttribute("data-top");
  });
});

describe("FilterMenu", () => {
  function Host({ onChange }: { onChange?: (c: ReadonlySet<EventClass> | null) => void }) {
    const [classes, setClasses] = useState<ReadonlySet<EventClass> | null>(null);
    const set = (c: ReadonlySet<EventClass> | null) => {
      setClasses(c);
      onChange?.(c);
    };
    return (
      <>
        <FilterMenu
          classes={classes}
          onToggle={(c) => {
            const cur = new Set(classes ?? CLASS_ORDER);
            if (cur.has(c)) cur.delete(c);
            else cur.add(c);
            set(cur.size === CLASS_ORDER.length ? null : cur);
          }}
          onReset={() => set(null)}
        />
        <button type="button">elsewhere</button>
      </>
    );
  }
  const button = () => screen.getByRole("button", { name: /^Filter/ });

  it("is closed at first, opens on click and exposes every event class", async () => {
    const user = userEvent.setup();
    render(<Host />);
    expect(button()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("group", { name: "Event classes" })).toBeNull();
    await user.click(button());
    expect(button()).toHaveAttribute("aria-expanded", "true");
    const group = screen.getByRole("group", { name: "Event classes" });
    expect(within(group).getAllByRole("checkbox")).toHaveLength(CLASS_ORDER.length);
    expect(within(group).getByLabelText("Model calls")).toBeChecked();
  });

  it("is keyboard operable: Enter opens it, Tab reaches the checkboxes, Escape closes it and returns focus", async () => {
    const user = userEvent.setup();
    render(<Host />);
    await user.tab();
    expect(button()).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(button()).toHaveAttribute("aria-expanded", "true");
    await user.tab();
    expect(screen.getByLabelText("Run & agent")).toHaveFocus();
    await user.keyboard(" ");
    expect(screen.getByLabelText("Run & agent")).not.toBeChecked();
    await user.keyboard("{Escape}");
    expect(button()).toHaveAttribute("aria-expanded", "false");
    expect(button()).toHaveFocus();
  });

  it("closes on a click or Tab outside", async () => {
    const user = userEvent.setup();
    render(<Host />);
    await user.click(button());
    await user.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(button()).toHaveAttribute("aria-expanded", "false");
    await user.click(button());
    await user.tab({ shift: true });
    await user.tab({ shift: true });
    expect(button()).toHaveAttribute("aria-expanded", "false");
  });

  it("shows how many classes are hidden and resets them", async () => {
    const user = userEvent.setup();
    const seen = vi.fn();
    render(<Host onChange={seen} />);
    expect(button()).toHaveAccessibleName("Filter ▾".replace(" ▾", ""));
    await user.click(button());
    await user.click(screen.getByLabelText("Model calls"));
    await user.click(screen.getByLabelText("Tools"));
    expect(button()).toHaveAccessibleName("Filter 2");
    expect(button()).toHaveAttribute("title", "2 event classes hidden");
    const classes = seen.mock.lastCall![0] as ReadonlySet<EventClass>;
    expect(classes.has("llm")).toBe(false);
    expect(classes.has("tool")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Show all classes" }));
    expect(button()).toHaveAccessibleName("Filter");
    expect(screen.getByRole("button", { name: "Show all classes" })).toBeDisabled();
  });
});

describe("the waterfall's fault line", () => {
  const events = [
    ...tool(1, 0, 10, { name: "ok_tool" }),
    ...tool(2, 12, 20, { name: "bad_tool", status: "error" }),
  ];
  // the failing span's closer carries status "error"
  events[events.length - 1] = { ...events[events.length - 1]!, status: "error" };
  const spans = [...deriveSpans(events).values()];
  const index = new SpanIndex();
  index.update(events);
  const render_ = (extra: Partial<React.ComponentProps<typeof Waterfall>> = {}) =>
    render(
      <Waterfall
        spans={spans}
        bounds={{ startMs: BASE, endMs: BASE + 30_000 }}
        spanIdOf={(id) => index.spanIdOf(id)}
        selectedEventId={null}
        firstErrorId={null}
        faultAtMs={BASE + 20_000}
        faultStep={4}
        agentFilter={null}
        onSelect={() => {}}
        onActivate={() => {}}
        onEscape={() => {}}
        onJumpToError={() => {}}
        {...extra}
      />,
    );

  it("cuts across the lanes at the first-error time, with a label and a tinted region after it", () => {
    render_();
    const line = document.querySelector<HTMLElement>(".wf-fault-line")!;
    expect(line).not.toBeNull();
    expect(document.querySelector(".wf-fault-region")).not.toBeNull();
    expect(document.querySelector(".wf-fault-flag")).toHaveTextContent("first error · step 4");
    const pct = parseFloat(line.style.left);
    expect(pct).toBeGreaterThan(0);
    expect(pct).toBeLessThanOrEqual(100);
    // both layers are decoration
    for (const l of document.querySelectorAll(".wf-fault-layer, .wf-fault-strip"))
      expect(l).toHaveAttribute("aria-hidden", "true");
  });

  it("is absent for a run with no error", () => {
    render_({ faultAtMs: null });
    expect(document.querySelector(".wf-fault-line, .wf-fault-flag, .wf-fault-strip")).toBeNull();
  });

  it("is placed by time, so zooming moves it and a window that excludes it hides it", async () => {
    const user = userEvent.setup();
    render_({ faultAtMs: BASE + 8_000 });
    const before = parseFloat(document.querySelector<HTMLElement>(".wf-fault-line")!.style.left);
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    const after = parseFloat(document.querySelector<HTMLElement>(".wf-fault-line")!.style.left);
    expect(after).not.toBe(before);
    for (let i = 0; i < 6; i++) await user.click(screen.getByRole("button", { name: "Pan later" }));
    expect(document.querySelector(".wf-fault-line")).toBeNull();
  });

  it("successful rows draw no status text but keep it for assistive technology", () => {
    render_();
    const ok = screen.getAllByRole("row").find((r) => r.textContent?.includes("ok_tool"))!;
    expect(ok.querySelector(".wf-status")).toBeNull();
    expect(ok.querySelector(".sr-only")).toHaveTextContent("success");
    const bad = screen.getAllByRole("row").find((r) => r.textContent?.includes("bad_tool"))!;
    expect(bad.querySelector(".wf-status")).toHaveTextContent("error");
  });
});
