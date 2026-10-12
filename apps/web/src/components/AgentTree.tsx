"use client";

import { formatCost, formatDuration, formatInt } from "@/lib/format";
import type { AgentTreeRow } from "@/lib/agents";
import { agentChip } from "@/lib/waterfall";

const STATE_TEXT: Record<string, string> = {
  spawned: "never started",
  started: "running or no end recorded",
  completed: "completed",
  failed: "failed",
  mixed: "mixed",
};

/**
 * Who delegated to whom, with state, counts, cost and duration per agent. Clicking an agent filters the timeline to its
 * events and focuses its lane. Every value is untrusted text.
 */
export function AgentTree({
  rows,
  active,
  onToggle,
  onClear,
}: {
  rows: readonly AgentTreeRow[];
  active: ReadonlySet<string> | null;
  onToggle: (agentId: string) => void;
  onClear: () => void;
}) {
  return (
    <section aria-labelledby="agents-h" className="agent-tree" data-testid="agent-tree">
      <h2 id="agents-h">Agents ({rows.length})</h2>
      <ul>
        {rows.map((a) => {
          const on = active?.has(a.agentId) ?? false;
          const detail = `${STATE_TEXT[a.state] ?? a.state} · ${formatInt(a.eventCount)} events · ${formatInt(a.llmCalls)} model calls · ${formatInt(a.toolCalls)} tool calls · ${
            a.errorCount > 0 ? `${a.errorCount} error${a.errorCount === 1 ? "" : "s"} · ` : ""
          }${formatCost(a.costUsd)} · ${formatDuration(a.durationMs)}`;
          return (
            <li key={a.agentId} style={{ paddingLeft: `${Math.min(a.depth, 8) * 1.1}rem` }}>
              <button
                type="button"
                aria-pressed={on}
                className="agent-item"
                title={`${agentChip(a.agentId)}: ${detail}`}
                data-error={a.errorCount > 0 || a.state === "failed" || undefined}
                onClick={() => onToggle(a.agentId)}
              >
                <span className="agent-name">{agentChip(a.agentId)}</span>{" "}
                <span className="muted agent-detail">{detail}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {active && active.size > 0 && (
        <button type="button" onClick={onClear}>
          Show all agents
        </button>
      )}
    </section>
  );
}
