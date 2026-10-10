import type { EventOut } from "@/lib/api/types";
import { isWithheld } from "./withheld";

/** What the agent is doing right now, read from the newest event (the run record lags by about a second). */
export type LiveStatus = { label: string; tone: "work" | "wait" | "done" | "bad" };

const attr = (e: EventOut, key: string): string | null => {
  if (isWithheld(e, key)) return null; // "Reading files", not a notice shaped like a path
  const v = e.attributes[key];
  return typeof v === "string" && v ? v : null;
};
const short = (s: string, n = 40): string => (s.length > n ? `…${s.slice(-(n - 1))}` : s);

export function liveStatus(events: readonly EventOut[]): LiveStatus | null {
  const last = events[events.length - 1];
  if (!last) return null;
  const type = last.event_type;
  switch (type) {
    case "run.completed":
      return last.status === "error" || last.status === "timeout"
        ? { label: "Failed", tone: "bad" }
        : { label: "Done", tone: "done" };
    case "run.failed":
      return { label: "Failed", tone: "bad" };
    case "run.cancelled":
      return { label: "Cancelled", tone: "bad" };
    case "approval.requested":
      return { label: "Waiting for approval", tone: "wait" };
    case "retry.attempted":
      return { label: "Retrying", tone: "work" };
    case "llm.request.started": {
      const model = attr(last, "llm.model");
      return { label: model ? `Calling model ${model}` : "Calling model", tone: "work" };
    }
    case "tool.call.started": {
      const tool = attr(last, "tool.name");
      return { label: tool ? `Running tool ${tool}` : "Running tool", tone: "work" };
    }
    case "shell.command.started":
      return { label: "Running a command", tone: "work" };
    case "file.read": {
      const path = attr(last, "file.path");
      return { label: path ? `Reading ${short(path)}` : "Reading files", tone: "work" };
    }
    case "file.created":
    case "file.modified":
    case "file.deleted": {
      const path = attr(last, "file.path");
      return { label: path ? `Editing ${short(path)}` : "Editing files", tone: "work" };
    }
    case "llm.request.completed":
    case "llm.request.failed":
    case "tool.call.completed":
    case "tool.call.failed":
    case "shell.command.completed":
    case "shell.command.failed":
    case "approval.granted":
    case "approval.denied":
      return { label: "Planning", tone: "work" };
    default:
      return { label: "Working", tone: "work" };
  }
}
