import { describe, expect, it } from "vitest";
import { stressRun } from "@/fixtures/runs";
import { mergeEvents } from "@/lib/ordering";
import { deriveSpans, SpanIndex } from "@/lib/spans";
import { buildTree, criticalPath, runBounds, treeRows } from "@/lib/waterfall";

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]!;
function time<T>(fn: () => T, runs = 5): { ms: number; value: T } {
  const samples: number[] = [];
  let value!: T;
  for (let i = 0; i < runs; i++) {
    const t = performance.now();
    value = fn();
    samples.push(performance.now() - t);
  }
  return { ms: median(samples), value };
}

/**
 * Records (does not tightly gate) the browser-side cost on the 10,000-event stress run: the numbers go in
 * docs/benchmarks/phase-9-waterfall.md. The gates are generous because CI runners are shared.
 */
describe("waterfall cost on the 10,000-event stress run", () => {
  const { run, events } = stressRun();

  it("derives, indexes incrementally and lays out within generous bounds", () => {
    const scratch = time(() => deriveSpans(events));
    const spans = [...scratch.value.values()];
    const index = new SpanIndex();
    // 20 live batches of 500 appended to a growing array, as the live merge would hand them over
    let merged = events.slice(0, events.length - 20 * 500);
    index.update(merged);
    const steps: number[] = [];
    for (let b = 0; b < 20; b++) {
      merged = mergeEvents(merged, events.slice(merged.length, merged.length + 500)).events;
      const t = performance.now();
      index.update(merged);
      steps.push(performance.now() - t);
    }
    const tree = time(() => buildTree(spans, runBounds(run)));
    const rows = time(() => treeRows(tree.value, new Set(), 2));
    const path = time(() => criticalPath(tree.value));
    const report = {
      events: events.length,
      spans: spans.length,
      deriveFromScratchMs: +scratch.ms.toFixed(1),
      spanIndexUpdateMedianMs: +median(steps).toFixed(1),
      buildTreeMs: +tree.ms.toFixed(1),
      treeRowsMs: +rows.ms.toFixed(1),
      criticalPathMs: +path.ms.toFixed(1),
    };
    console.info(`waterfall-perf ${JSON.stringify(report)}`);
    expect(spans.length).toBeGreaterThan(2000);
    expect(scratch.ms).toBeLessThan(1500);
    expect(median(steps)).toBeLessThan(1500);
    expect(tree.ms + rows.ms + path.ms).toBeLessThan(1500);
    expect(index.update(merged).length).toBe(spans.length);
  });
});
