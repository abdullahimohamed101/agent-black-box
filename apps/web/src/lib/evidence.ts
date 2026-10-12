import type { EventOut } from "@/lib/api/types";
import { artifactId } from "@/lib/coding";
import { num } from "@/lib/format";
import { contentText } from "@/lib/withheld";

export type EvidenceKind = "diff" | "stdout" | "stderr";

export type EvidenceRow = {
  /** Unique per artifact reference: an event can carry a diff, stdout and stderr. */
  key: string;
  kind: EvidenceKind;
  eventId: string;
  eventType: string;
  sequence: number | null;
  occurredAt: string;
  /** The file path, command or branch the artifact belongs to, or the withheld wording; null when the event names none. */
  what: string | null;
  /** Size announced by the event, when it says. */
  bytes: number | null;
  artifactId: string;
};

type Source = { kind: EvidenceKind; ref: string; bytes: string | null };
const RANK: Record<EvidenceKind, number> = { diff: 0, stdout: 1, stderr: 2 };
const SOURCES: Source[] = [
  { kind: "diff", ref: "diff.artifact", bytes: null },
  { kind: "stdout", ref: "shell.stdout_artifact", bytes: "shell.stdout_bytes" },
  { kind: "stderr", ref: "shell.stderr_artifact", bytes: "shell.stderr_bytes" },
];

/** What the artifact is about, as display text. Content attributes go through `contentText`, so a withheld value stays withheld. */
function subject(e: EventOut, kind: EvidenceKind): string | null {
  if (kind === "diff") {
    const family = e.event_type.split(".")[0];
    return family === "git" ? contentText(e, "git.branch") : contentText(e, "file.path");
  }
  return contentText(e, "shell.command");
}

/**
 * The artifacts a run captured, one row per reference, in the order the events happened. Derived from the loaded events
 * only (INV-2): no request, no content. The text behind an artifact is fetched by the inspector, on request. An id that
 * is not a well-formed `artifact://` reference is not evidence and is skipped.
 */
export function evidenceRows(events: readonly EventOut[]): EvidenceRow[] {
  const rows: EvidenceRow[] = [];
  for (const e of events) {
    for (const s of SOURCES) {
      const id = artifactId(e.attributes[s.ref]);
      if (!id) continue;
      rows.push({
        key: `${e.event_id}:${s.kind}`,
        kind: s.kind,
        eventId: e.event_id,
        eventType: e.event_type,
        sequence: e.sequence ?? null,
        occurredAt: e.occurred_at,
        what: subject(e, s.kind),
        bytes: s.bytes ? num(e.attributes[s.bytes]) : null,
        artifactId: id,
      });
    }
  }
  return rows.sort(
    (a, b) =>
      (a.sequence ?? Infinity) - (b.sequence ?? Infinity) ||
      Date.parse(a.occurredAt) - Date.parse(b.occurredAt) ||
      RANK[a.kind] - RANK[b.kind] ||
      a.eventId.localeCompare(b.eventId),
  );
}
