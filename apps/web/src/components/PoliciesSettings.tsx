"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import {
  COOPERATIVE_NOTE,
  MAX_PUBLISH_COMMENT_CHARS,
  STARTER_POLICY,
  sourceText,
  tooLargeForWeb,
  type PolicyIssue,
} from "@/lib/policy";
import { useDryRun, usePolicyCurrent, usePublishPolicy, validatePolicy } from "@/lib/policyApi";
import { LEAVE_TEXT, useUnsavedGuard } from "@/lib/useUnsavedGuard";
import { PolicyDryRunPanel } from "./PolicyDryRunPanel";
import { PolicyEditor, type EditorHandle } from "./PolicyEditor";
import { PolicyHistory } from "./PolicyHistory";
import { ActionError, Forbidden } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/policy.css";

const DEBOUNCE_MS = 400;

type Check =
  | { state: "idle" }
  | { state: "checking" }
  | { state: "ok"; rules: number | null }
  | { state: "issues"; issues: PolicyIssue[] }
  | { state: "error" };

/**
 * Policies settings: one policy per scope (the workspace base, then each project), edited as plain text. The text is
 * checked by the API as you type (the page never judges the language itself), a draft can be dry-run against the project's
 * recorded runs, and publishing makes a new immutable version that is written to the audit log. Enforcement is
 * cooperative and the page says so in its first lines.
 */
export function PoliciesSettings() {
  const ws = useWorkspace();
  const canWrite = useCan("policy.write");
  const canAudit = useCan("audit.read");
  const pathname = usePathname();
  const sp = useSearchParams();
  const param = sp?.get("project") ?? "";
  const project = param ? ws.projects.find((p) => p.slug === param || p.id === param) : undefined;
  const scopeId = project?.id ?? null;
  const [dirty, setDirty] = useState(false);
  const [published, setPublished] = useState<number | null>(null);
  // Bumped by an explicit reload, so the editor starts again from the published text even if nobody else published.
  const [reloads, setReloads] = useState(0);
  const pickerId = useId();
  const q = usePolicyCurrent(scopeId);

  useUnsavedGuard(dirty);

  const changeScope = (slug: string) => {
    if (dirty && !window.confirm(LEAVE_TEXT)) return;
    setDirty(false);
    setPublished(null);
    window.history.pushState(
      null,
      "",
      slug ? `${pathname}?project=${encodeURIComponent(slug)}` : pathname,
    );
  };

  const denied = q.error instanceof ApiRequestError && q.error.status === 403;
  if (denied) return <Forbidden what="policies" />;

  return (
    <div className="pol-page">
      <div className="page-head">
        <h1>Policies</h1>
        <p className="muted">
          Rules for what an agent may do. The first rule that matches decides; anything that matches
          no rule is allowed.
        </p>
      </div>
      <p className="pol-guarantee" role="note" data-testid="cooperative-note">
        {COOPERATIVE_NOTE}
      </p>
      <p className="pol-bar">
        <label htmlFor={pickerId}>Scope </label>
        <select
          id={pickerId}
          value={project?.slug ?? ""}
          onChange={(e) => changeScope(e.target.value)}
        >
          <option value="">Workspace policy (applies to projects without their own)</option>
          {ws.projects.map((p) => (
            <option key={p.id} value={p.slug}>
              {p.name}
            </option>
          ))}
        </select>
      </p>
      {q.isPending ? (
        <Loading label="Loading the policy" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <PolicyPane
          key={`${scopeId}|${q.data.source}|${q.data.policy?.version ?? 0}|${reloads}`}
          scopeId={scopeId}
          current={q.data}
          canWrite={canWrite}
          canAudit={canAudit}
          projects={ws.projects}
          workspaceSlug={ws.workspace.slug}
          published={published}
          onPublished={setPublished}
          onDirty={setDirty}
          onReload={async () => {
            await q.refetch();
            setReloads((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

function PolicyPane({
  scopeId,
  current,
  canWrite,
  canAudit,
  projects,
  workspaceSlug,
  published,
  onPublished,
  onDirty,
  onReload,
}: {
  scopeId: string | null;
  current: NonNullable<ReturnType<typeof usePolicyCurrent>["data"]>;
  canWrite: boolean;
  canAudit: boolean;
  projects: { id: string; slug: string; name: string }[];
  workspaceSlug: string;
  published: number | null;
  onPublished: (v: number) => void;
  onDirty: (d: boolean) => void;
  onReload: () => Promise<void>;
}) {
  const client = useQueryClient();
  const initial = current.policy?.document ?? "";
  // The version this draft is based on is the scope's own: an inherited workspace text is version 0 of a project.
  const own =
    current.policy != null &&
    (scopeId === null ? current.source === "workspace" : current.source === "project");
  const baseVersion = own ? current.policy!.version : 0;
  const [draft, setDraft] = useState(initial);
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const [comment, setComment] = useState("");
  const [dryProject, setDryProject] = useState(scopeId ?? projects[0]?.id ?? "");
  const editor = useRef<EditorHandle>(null);
  const statusId = useId();
  const commentId = useId();
  const dryId = useId();
  const publish = usePublishPolicy(scopeId, onPublished);
  const dry = useDryRun();
  const dirty = draft !== initial;
  const seq = useRef(0);

  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  // Debounced check by the API. A stale answer (the text moved on) is dropped by the sequence number.
  useEffect(() => {
    if (!canWrite || draft === initial) return;
    const mine = ++seq.current;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      setCheck({ state: "checking" });
      validatePolicy(draft, ctrl.signal)
        .then((r) => {
          if (seq.current !== mine) return;
          setCheck(
            r.valid ? { state: "ok", rules: r.rule_count } : { state: "issues", issues: r.issues },
          );
        })
        .catch(() => {
          if (seq.current === mine && !ctrl.signal.aborted) setCheck({ state: "error" });
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [draft, initial, canWrite]);

  const effective: Check = draft === initial ? { state: "idle" } : check;
  // The text the check answered for may be older than the editor by up to DEBOUNCE_MS: publishing waits for `ok`.
  const issues = effective.state === "issues" ? effective.issues : [];
  const tooBig = tooLargeForWeb(draft);
  const conflict =
    publish.error instanceof ApiRequestError && publish.error.code === "POLICY_VERSION_CONFLICT"
      ? publish.error
      : null;
  const invalidOnPublish =
    publish.error instanceof ApiRequestError && publish.error.code === "POLICY_INVALID"
      ? ((publish.error.details["issues"] as PolicyIssue[] | undefined) ?? [])
      : null;
  const shownIssues = issues.length > 0 ? issues : (invalidOnPublish ?? []);
  const canPublish =
    canWrite && dirty && effective.state === "ok" && !tooBig && !publish.isPending && !conflict;

  const runHref = (runId: string, eventId?: string) => {
    const p = projects.find((x) => x.id === (scopeId ?? dryProject));
    return `/w/${workspaceSlug}/projects/${p?.slug ?? "all"}/runs/${runId}${eventId ? `?event=${eventId}` : ""}`;
  };

  const start = () => {
    if (
      draft.trim() !== "" &&
      draft !== STARTER_POLICY &&
      !window.confirm("Replace the text in the editor with the example?")
    )
      return;
    setDraft(STARTER_POLICY);
  };

  const status =
    effective.state === "checking"
      ? "Checking…"
      : effective.state === "ok"
        ? `Valid${effective.rules != null ? `: ${effective.rules} rule${effective.rules === 1 ? "" : "s"}` : ""}.`
        : effective.state === "issues"
          ? `${effective.issues.length} problem${effective.issues.length === 1 ? "" : "s"} to fix before publishing.`
          : effective.state === "error"
            ? "The policy could not be checked right now."
            : dirty
              ? ""
              : own
                ? "Unchanged from the published version."
                : "";

  return (
    <>
      <p className="pol-meta" data-testid="policy-meta">
        {current.policy ? (
          <>
            Showing <strong>version {current.policy.version}</strong> of{" "}
            {sourceText(current.source, scopeId === null)}
            {" · "}published {current.policy.created_at.slice(0, 16).replace("T", " ")} UTC by{" "}
            <code>{current.policy.created_by}</code>
            {" · "}
            {current.policy.rule_count} rule{current.policy.rule_count === 1 ? "" : "s"}
            {current.policy.comment ? ` · “${current.policy.comment}”` : ""}
          </>
        ) : (
          <>No policy has been published for this scope; everything is allowed.</>
        )}
      </p>
      {!own && current.policy && scopeId !== null && (
        <p className="muted" data-testid="inherited-note">
          This project has no policy of its own, so it follows the workspace policy. Publishing here
          creates the project&apos;s own policy, which replaces the workspace one for this project.
        </p>
      )}
      {!canWrite && (
        <p className="notice" role="note" data-testid="readonly-note">
          You can read policies but not change them. Owners and admins edit and publish.
        </p>
      )}
      {!current.policy && !dirty && canWrite ? (
        <Empty
          title="No policy yet"
          motif="shield"
          action={
            <button type="button" onClick={start}>
              Start from the example
            </button>
          }
        >
          Start from the example and edit it, or write your own.
        </Empty>
      ) : (
        <>
          <PolicyEditor
            ref={editor}
            value={draft}
            onChange={canWrite ? setDraft : undefined}
            readOnly={!canWrite}
            issues={shownIssues}
            label={canWrite ? "Policy text" : "Policy text (read only)"}
            describedBy={statusId}
          />
          <p
            id={statusId}
            className="pol-status"
            role="status"
            aria-live="polite"
            data-testid="check-status"
          >
            {status}
            {tooBig && " This policy is too large to publish from the web editor."}
          </p>
          {shownIssues.length > 0 && (
            <ul className="pol-issues" aria-label="Problems in the policy" data-testid="issues">
              {shownIssues.map((i, n) => (
                <li key={`${i.line}:${i.column}:${i.code}:${n}`}>
                  <button type="button" onClick={() => editor.current?.goTo(i.line, i.column)}>
                    <span className="pol-pos">
                      {i.line}:{i.column}
                    </span>
                    <span>{i.message}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {canWrite && (
        <div className="pol-actions">
          <button type="button" onClick={start}>
            Start from the example
          </button>
          {scopeId === null && (
            <label htmlFor={dryId}>
              Dry run on project
              <select id={dryId} value={dryProject} onChange={(e) => setDryProject(e.target.value)}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            disabled={
              dry.isPending || !dryProject || draft.trim() === "" || effective.state === "issues"
            }
            onClick={() => dry.mutate({ document: draft, projectId: scopeId ?? dryProject })}
          >
            {dry.isPending ? "Running…" : "Dry run"}
          </button>
          <label className="pol-comment" htmlFor={commentId}>
            Comment (optional)
            <input
              id={commentId}
              type="text"
              value={comment}
              maxLength={MAX_PUBLISH_COMMENT_CHARS}
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!canPublish}
            onClick={() => publish.mutate({ document: draft, baseVersion, comment })}
          >
            {publish.isPending ? "Publishing…" : "Publish"}
          </button>
        </div>
      )}
      {conflict && (
        <p role="alert" className="notice" data-state="denied" data-testid="conflict">
          Someone published a newer version
          {typeof conflict.details["current_version"] === "number"
            ? ` (version ${conflict.details["current_version"]})`
            : ""}
          . Reload to continue from it; your draft in the editor will be replaced.{" "}
          <button
            type="button"
            onClick={() => {
              publish.reset();
              void client.invalidateQueries({ queryKey: ["policy", "versions"] });
              void onReload();
            }}
          >
            Reload the latest version
          </button>
        </p>
      )}
      {!conflict && !invalidOnPublish && <ActionError error={publish.error} />}
      {invalidOnPublish && (
        <p role="alert" className="notice" data-state="denied">
          The API found problems in this policy. They are listed above.
        </p>
      )}
      {published != null && !dirty && (
        <p role="status" className="notice" data-testid="published">
          Published version {published}. It applies to the next decision an agent asks for.
        </p>
      )}
      {dry.error && <ActionError error={dry.error} />}
      {dry.data && <PolicyDryRunPanel result={dry.data} runHref={runHref} />}
      <PolicyHistory
        projectId={scopeId}
        onUseAsDraft={canWrite ? (text) => setDraft(text) : undefined}
      />
      <p className="muted pol-audit">
        Publishing is written to the{" "}
        {canAudit ? (
          <Link href={`/w/${workspaceSlug}/settings/audit`}>audit log</Link>
        ) : (
          "audit log"
        )}{" "}
        (who, when and which version), never the policy text.
      </p>
    </>
  );
}
