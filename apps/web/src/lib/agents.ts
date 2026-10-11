import type { EventOut } from "@/lib/api/types";
import { num } from "./format";
import { isError } from "./timeline";

/**
 * The agent hierarchy of a run for the agent tree. `summary.agents` is derived by the API (detail endpoint only, at
 * most 50 entries, spawned-only agents included); runs summarised before Phase 9 have none, and then the tree is built
 * from the events the page holds. All values are untrusted text: shown as text, ids clipped to 64 characters.
 */

export type AgentEntry = {
  agentId: string;
  parentAgentId: string | null;
  /** "spawned" (never started), "started", "completed", "failed", or "mixed" for the folded "(other)" row. */
  state: string;
  eventCount: number;
  llmCalls: number;
  toolCalls: number;
  errorCount: number;
  costUsd: number | null;
  firstEventAt: string | null;
  lastEventAt: string | null;
};

export type AgentTreeRow = AgentEntry & { depth: number; durationMs: number | null };

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** `summary.agents`, read defensively (the summary is an open JSON object). */
export function summaryAgents(summary: Record<string, unknown>): AgentEntry[] | null {
  const raw = summary["agents"];
  if (!Array.isArray(raw)) return null;
  const out: AgentEntry[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const a = item as Record<string, unknown>;
    const agentId = str(a["agent_id"]);
    if (!agentId) continue;
    out.push({
      agentId,
      parentAgentId: str(a["parent_agent_id"]),
      state: str(a["state"]) ?? "started",
      eventCount: num(a["event_count"]) ?? 0,
      llmCalls: num(a["llm_calls"]) ?? 0,
      toolCalls: num(a["tool_calls"]) ?? 0,
      errorCount: num(a["error_count"]) ?? 0,
      costUsd: num(a["estimated_cost_usd"]) ?? (Number(a["estimated_cost_usd"]) || null),
      firstEventAt: str(a["first_event_at"]),
      lastEventAt: str(a["last_event_at"]),
    });
  }
  return out;
}

/** Agents seen in the events, in order of first appearance: the fallback for runs with no `summary.agents`. */
export function agentsFromEvents(events: readonly EventOut[]): AgentEntry[] {
  const byId = new Map<string, AgentEntry>();
  for (const e of events) {
    let a = byId.get(e.agent_id);
    if (!a) {
      a = {
        agentId: e.agent_id,
        parentAgentId: null,
        state: "started",
        eventCount: 0,
        llmCalls: 0,
        toolCalls: 0,
        errorCount: 0,
        costUsd: null,
        firstEventAt: e.occurred_at,
        lastEventAt: e.occurred_at,
      };
      byId.set(e.agent_id, a);
    }
    a.eventCount++;
    if (/^llm\.request\.(completed|failed)$/.test(e.event_type)) a.llmCalls++;
    if (/^tool\.call\.(completed|failed)$/.test(e.event_type)) a.toolCalls++;
    if (isError(e)) a.errorCount++;
    if (a.firstEventAt === null || e.occurred_at < a.firstEventAt) a.firstEventAt = e.occurred_at;
    if (a.lastEventAt === null || e.occurred_at > a.lastEventAt) a.lastEventAt = e.occurred_at;
  }
  return [...byId.values()];
}

/** Agents, parents before children (depth-first, siblings in the given order). A parent loop cannot hide an agent. */
export function agentTreeRows(agents: readonly AgentEntry[]): AgentTreeRow[] {
  const ids = new Set(agents.map((a) => a.agentId));
  const kids = new Map<string | null, AgentEntry[]>();
  for (const a of agents) {
    const parent =
      a.parentAgentId && a.parentAgentId !== a.agentId && ids.has(a.parentAgentId)
        ? a.parentAgentId
        : null;
    const list = kids.get(parent);
    if (list) list.push(a);
    else kids.set(parent, [a]);
  }
  const rows: AgentTreeRow[] = [];
  const seen = new Set<string>();
  const walk = (start: AgentEntry[], depth: number) => {
    const stack: [AgentEntry, number][] = [...start].reverse().map((a) => [a, depth]);
    while (stack.length > 0) {
      const [a, d] = stack.pop()!;
      if (seen.has(a.agentId)) continue;
      seen.add(a.agentId);
      const first = a.firstEventAt ? Date.parse(a.firstEventAt) : NaN;
      const last = a.lastEventAt ? Date.parse(a.lastEventAt) : NaN;
      rows.push({
        ...a,
        depth: d,
        durationMs:
          Number.isFinite(first) && Number.isFinite(last) ? Math.max(0, last - first) : null,
      });
      const children = kids.get(a.agentId) ?? [];
      for (let i = children.length - 1; i >= 0; i--) stack.push([children[i]!, d + 1]);
    }
  };
  walk(kids.get(null) ?? [], 0);
  // A parent loop (A under B under A) leaves both unreachable from a root: list them anyway, at the top level.
  walk(
    agents.filter((a) => !seen.has(a.agentId)),
    0,
  );
  return rows;
}
