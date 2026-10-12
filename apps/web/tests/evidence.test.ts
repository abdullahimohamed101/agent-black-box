import { describe, expect, it } from "vitest";
import type { EventOut } from "@/lib/api/types";
import { evidenceRows } from "@/lib/evidence";
import { WITHHELD_TEXT } from "@/lib/withheld";

let n = 0;
const ev = (
  event_type: string,
  attributes: Record<string, unknown> = {},
  extra: Partial<EventOut> = {},
): EventOut =>
  ({
    event_id: `evt_${String(++n).padStart(26, "0")}`,
    run_id: "run_X",
    trace_id: "trc_X",
    span_id: null,
    parent_span_id: null,
    agent_id: "coding-agent",
    event_type,
    occurred_at: `2026-10-07T10:00:${String(n % 60).padStart(2, "0")}Z`,
    sequence: n,
    status: null,
    duration_ms: null,
    attributes,
    tags: [],
    has_payload: false,
    payload: null,
    ...extra,
  }) as unknown as EventOut;

const art = (k: number) => `artifact://art_${String(k).padStart(26, "0")}`;

describe("evidenceRows", () => {
  it("is empty for a run with no artifacts", () => {
    expect(
      evidenceRows([ev("run.started"), ev("tool.call.started", { "tool.name": "x" })]),
    ).toEqual([]);
  });

  it("lists a diff per edit and stdout and stderr per command, in event order", () => {
    const events = [
      ev("file.modified", { "file.path": "app/session.py", "diff.artifact": art(1) }),
      ev("shell.command.failed", {
        "shell.command": "pytest -q",
        "shell.stdout_artifact": art(2),
        "shell.stdout_bytes": 1200,
        "shell.stderr_artifact": art(3),
        "shell.stderr_bytes": 80,
      }),
      ev("git.commit", { "git.branch": "fix/x", "diff.artifact": art(4) }),
    ];
    const rows = evidenceRows([...events].reverse());
    expect(rows.map((r) => [r.kind, r.what, r.bytes])).toEqual([
      ["diff", "app/session.py", null],
      ["stdout", "pytest -q", 1200],
      ["stderr", "pytest -q", 80],
      ["diff", "fix/x", null],
    ]);
    expect(rows[0]).toMatchObject({ eventId: events[0]!.event_id, eventType: "file.modified" });
    expect(new Set(rows.map((r) => r.key)).size).toBe(4);
  });

  it("shows the withheld wording for a path or command the API withheld, never the placeholder", () => {
    const rows = evidenceRows([
      ev("file.modified", { "file.path": "[withheld]", "diff.artifact": art(1) }, {
        withheld_attributes: ["file.path"],
      } as Partial<EventOut>),
      ev(
        "shell.command.completed",
        { "shell.command": "[withheld]", "shell.stdout_artifact": art(2) },
        { withheld_attributes: ["shell.command"] } as Partial<EventOut>,
      ),
    ]);
    expect(rows.map((r) => r.what)).toEqual([WITHHELD_TEXT, WITHHELD_TEXT]);
    expect(JSON.stringify(rows)).not.toContain("[withheld]");
  });

  it("ignores references that are not artifact ids", () => {
    expect(
      evidenceRows([
        ev("file.modified", { "diff.artifact": "artifact://../../etc/passwd" }),
        ev("shell.command.completed", { "shell.stdout_artifact": 42 }),
        ev("file.modified", { "diff.artifact": "https://example.test/x" }),
      ]),
    ).toEqual([]);
  });

  it("keeps a row whose event names no path as a dash, not as invented text", () => {
    const [row] = evidenceRows([ev("file.modified", { "diff.artifact": art(9) })]);
    expect(row!.what).toBeNull();
  });
});
