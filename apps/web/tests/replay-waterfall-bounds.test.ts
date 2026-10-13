import { describe, expect, it } from "vitest";
import { multiAgentRun } from "@/fixtures/runs";
import { deriveSpans } from "@/lib/spans";
import { buildTree, runBounds, timeDomain } from "@/lib/waterfall";
import { replayHeadline } from "@/lib/runHeadline";
import { projectAt } from "@/lib/replay";
import { failureRetryRun } from "@/fixtures/runs";
import { headlineModel } from "@/lib/runHeadline";

// Replay draws the waterfall from the events up to the playhead on the run's full axis: spans still open end at the
// playhead (`openEndMs`), not at the run's end, and the axis does not move.
describe("waterfall bounds in replay", () => {
  const f = multiAgentRun();
  const so = f.events.slice(0, 12);
  const t0 = Math.min(...f.events.map((e) => Date.parse(e.occurred_at)));
  const full = runBounds(f.run, t0);
  const spans = [...deriveSpans(so).values()];

  it("an open span ends at the playhead, and the axis keeps the whole run's extent", () => {
    const at = t0 + 4_000;
    const grown = buildTree(spans, { ...full, openEndMs: at });
    const live = buildTree(spans, full);
    const open = [...grown.nodes.values()].filter((n) => n.flags.unfinished);
    expect(open.length).toBeGreaterThan(0);
    for (const n of open) expect(n.endMs).toBeLessThanOrEqual(Math.max(at, n.startMs));
    for (const n of open) expect(live.nodes.get(n.id)!.endMs).toBeGreaterThan(n.endMs);
    expect(timeDomain(grown)).toEqual(timeDomain(live));
  });

  it("a later playhead makes the same open span longer", () => {
    const id = [...buildTree(spans, full).nodes.values()].find((n) => n.flags.unfinished)!.id;
    const a = buildTree(spans, { ...full, openEndMs: t0 + 3_000 }).nodes.get(id)!;
    const b = buildTree(spans, { ...full, openEndMs: t0 + 6_000 }).nodes.get(id)!;
    expect(b.endMs).toBeGreaterThan(a.endMs);
  });
});

describe("replayHeadline", () => {
  const f = failureRetryRun();
  const t0 = Math.min(...f.events.map((e) => Date.parse(e.occurred_at)));
  const live = headlineModel({ run: f.run, events: f.events, t0, eventsComplete: true });
  const ctx = { status: f.run.status, firstErrorId: f.run.first_error!.event_id };
  const errIdx = f.events.findIndex((e) => e.event_id === ctx.firstErrorId);

  it("before the first error it is a running headline and carries no failure", () => {
    const m = replayHeadline(live, projectAt(f.events, errIdx - 1, ctx));
    expect(m.kind).toBe("running");
    expect(m.failure).toBeNull();
    expect(m.lead).toMatch(/^Running · step \d+ · replay$/);
    expect(`${m.lead} ${m.detail}`).not.toMatch(/run_migration|ConnectionTimeout|Failed/);
  });

  it("once reached it names the first error and states only the figures so far", () => {
    const m = replayHeadline(live, projectAt(f.events, errIdx, ctx));
    expect(m.kind).toBe("failed");
    expect(m.lead).toMatch(
      /^First error at step \d+ \(\+6\.2 s\): run_migration, ConnectionTimeout$/,
    );
    expect(m.detail).toBe("No retries · $0.0189 so far");
  });
});
