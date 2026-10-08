import type { EventOut } from "@/lib/api/types";
import { num } from "./format";

/** Pure logic for coding-agent runs: risk labels, artifact references, sensitive paths and the run's story. */

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export const RISK_LABELS: Record<string, string> = {
  R0: "Observation only",
  R1: "Local, reversible change",
  R2: "External, reversible change",
  R3: "Privileged or destructive",
  R4: "Catastrophic or high impact",
};
export const riskLabel = (rc: string | null): string | null =>
  rc ? `${rc} ${RISK_LABELS[rc] ?? "unclassified"}` : null;
/** R3 and R4 draw attention; the product only observes (spec §83). */
export const riskTone = (rc: string | null): "ok" | "warn" | "bad" | "neutral" =>
  rc === "R3" || rc === "R4" ? "bad" : rc === "R2" ? "warn" : rc ? "ok" : "neutral";

const ARTIFACT_REF = /^artifact:\/\/(art_[0-9A-Za-z]{26})$/;
/** The artifact id from an `artifact://` attribute; anything else (including path tricks) is not an id. */
export const artifactId = (ref: unknown): string | null => {
  const m = typeof ref === "string" ? ARTIFACT_REF.exec(ref) : null;
  return m ? m[1]! : null;
};

export type CodingKind = "file" | "git" | "shell" | null;
export const codingKind = (e: EventOut): CodingKind => {
  const family = e.event_type.split(".")[0];
  return family === "file" || family === "git" || family === "shell" ? family : null;
};

const LABELS: [RegExp, string][] = [
  [
    /(^|\/)(\.github\/workflows\/|\.gitlab-ci\.ya?ml$|\.circleci\/|Jenkinsfile$)/,
    "CI/CD configuration",
  ],
  [
    /(^|\/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|uv\.lock|poetry\.lock|Cargo\.lock|Gemfile\.lock|go\.sum)$/,
    "dependency lock file",
  ],
  [
    /(^|[/_.-])(auth|oauth|session|login|jwt|credential|password|token)s?([/_.-]|$)/i,
    "authentication code",
  ],
];
/** Informational only (spec §26 "security use"): no finding is stored and nothing is blocked. */
export const sensitivePathLabel = (path: string | null): string | null =>
  path ? (LABELS.find(([re]) => re.test(path))?.[1] ?? null) : null;

export const shortHash = (h: unknown): string | null =>
  typeof h === "string" && h ? h.replace(/^sha256:/, "").slice(0, 10) : null;

export type StepKind = "read" | "model" | "edit" | "test" | "command" | "retry" | "git" | "error";
export type Step = {
  key: string;
  eventId: string;
  kind: StepKind;
  tone: "ok" | "bad" | "warn" | "neutral";
  label: string;
  detail: string;
};

const shortPath = (p: string | null) => p ?? "file";

/**
 * The run as a short numbered story ("read, model, edit, test fails, retry, edit, tests pass"). Only meaningful
 * events become steps: span-open events and plain bookkeeping are skipped. Order is the API's canonical order.
 */
export function buildStory(events: readonly EventOut[]): Step[] {
  const steps: Step[] = [];
  let attempt = 0;
  for (const e of events) {
    const a = e.attributes;
    const key = e.event_id;
    switch (e.event_type) {
      case "file.read":
        steps.push({
          key,
          eventId: key,
          kind: "read",
          tone: "neutral",
          label: "Read",
          detail: shortPath(str(a["file.path"])),
        });
        break;
      case "llm.request.completed":
        steps.push({
          key,
          eventId: key,
          kind: "model",
          tone: "neutral",
          label: "Model",
          detail: str(a["llm.model"]) ?? "model call",
        });
        break;
      case "file.created":
      case "file.modified":
      case "file.deleted": {
        const op = e.event_type.slice(5);
        const added = num(a["file.lines_added"]);
        const removed = num(a["file.lines_removed"]);
        steps.push({
          key,
          eventId: key,
          kind: "edit",
          tone: "neutral",
          label: `Edit (${op})`,
          detail: `${shortPath(str(a["file.path"]))}${added != null || removed != null ? ` +${added ?? 0} -${removed ?? 0}` : ""}`,
        });
        break;
      }
      case "shell.command.completed":
      case "shell.command.failed": {
        const failed = num(a["test.failed"]);
        const total = num(a["test.total"]);
        const code = num(a["shell.exit_code"]);
        if (str(a["test.framework"]) && total != null) {
          attempt += 1;
          const ok = (failed ?? 0) === 0 && (code ?? 0) === 0;
          const passed = num(a["test.passed"]) ?? 0;
          const names = Array.isArray(a["test.failing"]) ? (a["test.failing"] as string[]) : [];
          steps.push({
            key,
            eventId: key,
            kind: "test",
            tone: ok ? "ok" : "bad",
            label: `Tests, attempt ${attempt}: ${ok ? "pass" : "fail"}`,
            detail: ok
              ? `${passed} of ${total} passed`
              : `${failed ?? "?"} failed, ${passed} passed${names.length ? `: ${names.join(", ")}` : ""}`,
          });
        } else {
          steps.push({
            key,
            eventId: key,
            kind: "command",
            tone: code ? "bad" : "neutral",
            label: code ? `Command failed (exit ${code})` : "Command",
            detail: str(a["shell.command"]) ?? "",
          });
        }
        break;
      }
      case "retry.attempted": {
        const n = num(a["retry.attempt"]);
        steps.push({
          key,
          eventId: key,
          kind: "retry",
          tone: "warn",
          label: `Retry${n != null ? ` #${n}` : ""}`,
          detail: str(a["retry.reason"]) ?? "",
        });
        break;
      }
      case "git.branch_created":
      case "git.commit":
      case "git.push":
        steps.push({
          key,
          eventId: key,
          kind: "git",
          tone: "neutral",
          label:
            e.event_type === "git.push"
              ? "Push"
              : e.event_type === "git.commit"
                ? "Commit"
                : "Branch",
          detail:
            str(a["git.push_target"]) ??
            str(a["git.branch"]) ??
            str(a["git.commit_hash"])?.slice(0, 10) ??
            "",
        });
        break;
      default:
        if (e.event_type === "llm.request.failed" || e.event_type === "tool.call.failed")
          steps.push({
            key,
            eventId: key,
            kind: "error",
            tone: "bad",
            label: "Error",
            detail: str(a["error.type"]) ?? e.event_type,
          });
    }
  }
  return steps;
}

/** A run is a coding run when it edited files or ran commands; other runs do not show the story. */
export const isCodingRun = (events: readonly EventOut[]): boolean =>
  events.some((e) => e.event_type.startsWith("file.") && e.event_type !== "file.read") ||
  events.some((e) => e.event_type.startsWith("shell.command."));
