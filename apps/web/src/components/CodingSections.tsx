"use client";

import type { EventOut } from "@/lib/api/types";
import {
  artifactId,
  codingKind,
  riskLabel,
  riskTone,
  sensitivePathLabel,
  shortHash,
} from "@/lib/coding";
import { formatDuration, formatInt, num } from "@/lib/format";
import { contentText, isWithheld } from "@/lib/withheld";
import { ArtifactText } from "./ArtifactText";

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function Rows({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="fields">
      {rows
        .filter(([, v]) => v != null && v !== "")
        .map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
    </dl>
  );
}

function Badge({
  tone,
  children,
  testId,
}: {
  tone: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <span className={`badge badge-${tone}`} data-testid={testId}>
      {children}
    </span>
  );
}

function FileSection({ event }: { event: EventOut }) {
  const a = event.attributes;
  const path = contentText(event, "file.path");
  // The notice must not be matched as a path: only a real path earns a sensitive-path badge.
  const label = isWithheld(event, "file.path") ? null : sensitivePathLabel(path);
  const added = num(a["file.lines_added"]);
  const removed = num(a["file.lines_removed"]);
  const diff = artifactId(a["diff.artifact"]);
  const withheld = str(a["diff.withheld"]);
  return (
    <section aria-labelledby="d-file" data-testid="file-section">
      <h3 id="d-file">File</h3>
      <Rows
        rows={[
          ["Path", path && <code>{path}</code>],
          ["Operation", str(a["file.operation"]) ?? event.event_type.slice(5)],
          ["Language", str(a["file.language"])],
          ["Lines", added != null || removed != null ? `+${added ?? 0} -${removed ?? 0}` : null],
          [
            "Size",
            num(a["file.size_before"]) != null || num(a["file.size_after"]) != null
              ? `${formatInt(num(a["file.size_before"]))} → ${formatInt(num(a["file.size_after"]))} bytes`
              : null,
          ],
          [
            "Content hash",
            shortHash(a["file.hash_before"]) || shortHash(a["file.hash_after"])
              ? `${shortHash(a["file.hash_before"]) ?? "—"} → ${shortHash(a["file.hash_after"]) ?? "—"}`
              : null,
          ],
        ]}
      />
      {label && event.event_type !== "file.read" && (
        <p>
          <Badge tone="warn" testId="sensitive-path">
            ⚠ Touches {label}
          </Badge>
        </p>
      )}
      {event.event_type === "file.read" ? (
        <p className="muted">Reads record the path and a content hash, never the content.</p>
      ) : withheld ? (
        <p className="muted" data-testid="diff-withheld">
          Diff not stored:{" "}
          {withheld === "sensitive_path"
            ? "the path looks like it holds secrets"
            : "the file is too large"}
          .
        </p>
      ) : (
        <ArtifactText
          id={diff}
          label="diff"
          mode="diff"
          lang={str(a["file.language"])}
          defaultOpen
        />
      )}
    </section>
  );
}

function GitSection({ event }: { event: EventOut }) {
  const a = event.attributes;
  const diff = artifactId(a["diff.artifact"]);
  return (
    <section aria-labelledby="d-git" data-testid="git-section">
      <h3 id="d-git">Git</h3>
      <Rows
        rows={[
          ["Branch", contentText(event, "git.branch")],
          ["Base commit", str(a["git.base_commit"])?.slice(0, 10)],
          ["Commit", str(a["git.commit_hash"])?.slice(0, 10)],
          [
            "Changed files",
            num(a["git.changed_files"]) != null ? formatInt(num(a["git.changed_files"])) : null,
          ],
          ["Push target", contentText(event, "git.push_target")],
        ]}
      />
      {diff && <ArtifactText id={diff} label="diff" mode="diff" defaultOpen />}
    </section>
  );
}

function TestResult({ event }: { event: EventOut }) {
  const a = event.attributes;
  const total = num(a["test.total"]);
  if (!str(a["test.framework"]) || total == null) return null;
  const failed = num(a["test.failed"]) ?? 0;
  const failingHidden = isWithheld(event, "test.failing");
  const failing = Array.isArray(a["test.failing"])
    ? (a["test.failing"] as unknown[]).map(String)
    : [];
  return (
    <div data-testid="test-result">
      <p>
        <Badge tone={failed ? "bad" : "ok"}>{failed ? "✕ Tests failed" : "✓ Tests passed"}</Badge>{" "}
        <span>
          {num(a["test.passed"]) ?? 0} passed, {failed} failed, {num(a["test.skipped"]) ?? 0}{" "}
          skipped of {total} <span className="muted">({str(a["test.framework"])})</span>
        </span>
      </p>
      {failingHidden && (
        <p className="withheld" data-testid="failing-withheld">
          Failing test names: {contentText(event, "test.failing")}.
        </p>
      )}
      {failing.length > 0 && (
        <ul className="failing" aria-label="Failing tests">
          {failing.map((id) => (
            <li key={id}>
              <code>{id}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ShellPanel({ event }: { event: EventOut }) {
  const a = event.attributes;
  const rc = str(a["shell.risk_class"]);
  const code = num(a["shell.exit_code"]);
  const closed = event.event_type !== "shell.command.started";
  return (
    <section aria-labelledby="d-shell" data-testid="shell-panel">
      <h3 id="d-shell">Shell command</h3>
      {isWithheld(event, "shell.command") ? (
        <p className="withheld" data-testid="command-withheld">
          The command line is hidden by your role. You can see that a command ran and how it went,
          not what it said.
        </p>
      ) : (
        <pre className="cmd" tabIndex={0} aria-label="Command">
          {str(a["shell.command"]) ?? "(no command recorded)"}
        </pre>
      )}
      <Rows
        rows={[
          [
            "Working directory",
            contentText(event, "shell.cwd") && <code>{contentText(event, "shell.cwd")}</code>,
          ],
          [
            "Duration",
            event.duration_ms != null
              ? formatDuration(event.duration_ms)
              : num(a["shell.duration_ms"]) != null
                ? formatDuration(num(a["shell.duration_ms"]))
                : null,
          ],
          [
            "Exit code",
            closed && code != null ? (
              <Badge tone={code === 0 ? "ok" : "bad"} testId="exit-code">
                {code === 0 ? "✓" : "✕"} {code}
              </Badge>
            ) : null,
          ],
          [
            "Risk class",
            rc ? (
              <Badge tone={riskTone(rc)} testId="risk-class">
                {riskLabel(rc)}
              </Badge>
            ) : null,
          ],
          ["Category", str(a["shell.category"])],
        ]}
      />
      <TestResult event={event} />
      {closed && (
        <>
          <ArtifactText
            id={artifactId(a["shell.stdout_artifact"])}
            label="stdout"
            bytes={num(a["shell.stdout_bytes"])}
          />
          <ArtifactText
            id={artifactId(a["shell.stderr_artifact"])}
            label="stderr"
            bytes={num(a["shell.stderr_bytes"])}
          />
          {a["shell.output_truncated"] === true && (
            <p className="muted">Output was longer than the capture limit and is truncated.</p>
          )}
          <p className="muted">
            The environment of a command is never recorded, and secrets found in its output are
            redacted before storage.
          </p>
        </>
      )}
    </section>
  );
}

/** File, git and shell panels (spec §26, §27). Empty for other events. */
export function CodingSections({ event }: { event: EventOut }) {
  switch (codingKind(event)) {
    case "file":
      return <FileSection event={event} />;
    case "git":
      return <GitSection event={event} />;
    case "shell":
      return <ShellPanel event={event} />;
    default:
      return null;
  }
}
