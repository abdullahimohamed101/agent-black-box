import { describe, expect, it } from "vitest";
import { failureRetryRun, multiAgentRun } from "@/fixtures/runs";
import { deriveSpans } from "@/lib/spans";
import { agentOrder, buildTree, criticalPath, runBounds } from "@/lib/waterfall";

const treeOf = (f: ReturnType<typeof multiAgentRun>) =>
  buildTree([...deriveSpans(f.events).values()], runBounds(f.run));

describe("fixture runs through the waterfall", () => {
  it("multi-agent: five lanes, the tester nested under the coder and flagged as skewed", () => {
    const t = treeOf(multiAgentRun());
    expect(agentOrder(t).sort()).toEqual(["coder", "planner", "researcher", "reviewer", "tester"]);
    const byAgent = (a: string) =>
      [...t.nodes.values()].find((n) => n.span?.agentId === a && n.span.kind === "agent")!;
    const tester = byAgent("tester");
    expect(tester.parentId).toBe(byAgent("coder").id);
    expect(tester.flags.skewed).toBe(true);
    expect(byAgent("coder").flags.skewed).toBe(false);
    expect(byAgent("researcher").parentId).toBe(byAgent("planner").id);
    expect([...t.nodes.values()].some((n) => n.flags.unfinished)).toBe(false);
  });

  it("multi-agent: the critical path runs through the planner and its last finisher", () => {
    const f = multiAgentRun();
    const t = treeOf(f);
    const ids = criticalPath(t).spanIds;
    const agents = new Set([...ids].map((id) => t.nodes.get(id)!.span!.agentId));
    expect(agents.has("planner")).toBe(true);
    expect(agents.has("reviewer")).toBe(true);
  });

  it("the failure run's root span is finished and carries the error", () => {
    const f = failureRetryRun();
    const t = treeOf(f);
    const rootAgent = [...t.nodes.values()].find((n) => n.span?.kind === "agent")!;
    expect(rootAgent.flags.unfinished).toBe(false);
    expect(rootAgent.span!.status).toBe("error");
    expect(rootAgent.span!.hasError).toBe(true);
  });
});
