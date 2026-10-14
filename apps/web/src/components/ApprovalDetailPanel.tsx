"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import { formatTimestamp } from "@/lib/format";
import {
  MAX_COMMENT_CHARS,
  ageSeconds,
  approverText,
  decisionErrorText,
  leftText,
  type ApprovalDetail,
} from "@/lib/policy";
import { useApproval, useDecideApproval } from "@/lib/policyApi";
import { RiskMark, StateMark } from "./PolicyMarks";
import { ErrorState, Loading } from "./States";
import "../app/policy.css";

const ARG_CHARS = 20_000;

function Field({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{k}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * One request in full: the intent exactly as the API returns it (scrubbed resource and arguments, risk class, who may
 * decide, when it expires, which policy asked) and, for a person who may decide, Approve and Deny with an optional
 * comment and a confirm step. Every refusal is the API's fixed code in words; nothing about the request is echoed into an
 * error, and after a decision the panel shows the API's answer, not a guess.
 */
export function ApprovalDetailPanel({
  id,
  canDecide,
  now,
  runHref,
  projectName,
  onClose,
}: {
  id: string;
  canDecide: boolean;
  now: number;
  runHref: (runId: string, projectId: string) => string;
  projectName: (projectId: string) => string;
  onClose: () => void;
}) {
  const q = useApproval(id);
  const headRef = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const a = q.data;
  useEffect(() => {
    if (a) headRef.current?.focus();
    // Focus moves once, when the request first appears; a refresh must not steal it back from the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.id]);

  return (
    <aside
      className="apr-detail"
      aria-labelledby={titleId}
      data-testid="approval-detail"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !e.defaultPrevented) {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="apr-detail-head">
        <h2 id={titleId} ref={headRef} tabIndex={-1}>
          {a ? a.capability : "Approval request"}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close details">
          Close
        </button>
      </div>
      {q.isPending ? (
        <Loading label="Loading the request" />
      ) : q.isError ? (
        q.error instanceof ApiRequestError && q.error.status === 404 ? (
          <div role="alert" className="state" data-state="error">
            <p className="state-title">Not found</p>
            <p className="muted">{decisionErrorText("APPROVAL_NOT_FOUND", 404)}</p>
          </div>
        ) : (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        )
      ) : (
        <Body
          a={q.data}
          received={q.dataUpdatedAt}
          now={now}
          canDecide={canDecide}
          runHref={runHref(q.data.run_id, q.data.project_id)}
          project={projectName(q.data.project_id)}
        />
      )}
    </aside>
  );
}

function Body({
  a,
  received,
  now,
  canDecide,
  runHref,
  project,
}: {
  a: ApprovalDetail;
  received: number;
  now: number;
  canDecide: boolean;
  runHref: string;
  project: string;
}) {
  const left = a.state === "REQUESTED" ? leftText(ageSeconds(a.seconds_left, received, now)) : null;
  // Kept here, not in the form: a decision (or a lost race) replaces the form, and the answer must outlive it.
  const [outcome, setOutcome] = useState<
    { kind: "ok"; text: string } | { kind: "error"; error: ApiRequestError } | null
  >(null);
  // The form the person was in is gone once a decision lands: the answer takes the focus, so keys keep working in the panel.
  const answer = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (outcome) answer.current?.focus();
  }, [outcome]);
  const json =
    a.arguments && Object.keys(a.arguments).length > 0
      ? JSON.stringify(a.arguments, null, 2)
      : null;
  const command =
    a.capability === "shell.execute" && typeof a.arguments?.["command"] === "string"
      ? a.arguments["command"]
      : null;
  return (
    <>
      <p className="apr-state">
        <StateMark approval={a} />
        {left && <span className="apr-left-inline"> · {left}</span>}
      </p>
      <dl className="fields apr-fields">
        <Field k="Capability">
          <code>{a.capability}</code>
        </Field>
        <Field k="Resource">{a.resource ? <code>{a.resource}</code> : "—"}</Field>
        <Field k="Risk class">
          <RiskMark risk={a.risk_class} />
        </Field>
        <Field k="Policy">
          {a.policy_id} <span className="muted">v{a.policy_version}</span>
        </Field>
        <Field k="Approver needed">{approverText(a.approver_role)}</Field>
        <Field k="Expires">
          {formatTimestamp(a.expires_at)}
          <span className="muted">
            {" "}
            · if it expires, the action is {a.on_expiry === "allow" ? "allowed" : "denied"}
          </span>
        </Field>
        <Field k="Requested">{formatTimestamp(a.requested_at)}</Field>
        <Field k="Requested by">
          <Link href={runHref}>run …{a.run_id.slice(-8)}</Link>
          <span className="muted">
            {" "}
            · agent <code>{a.agent_id}</code> · {project}
          </span>
        </Field>
        {a.state !== "REQUESTED" && (
          <>
            {a.decided_at && (
              <Field k="Decided">
                {formatTimestamp(a.decided_at)}
                {a.decided_by && (
                  <span className="muted">
                    {" "}
                    by <code>{a.decided_by}</code>
                  </span>
                )}
              </Field>
            )}
            {a.closed_at && !a.decided_at && (
              <Field k="Closed">{formatTimestamp(a.closed_at)}</Field>
            )}
            <Field k="Comment">{a.comment ? a.comment : <span className="muted">None</span>}</Field>
            {a.state === "APPROVED" && (
              <Field k="Used">
                {a.consumed && a.consumed_at ? formatTimestamp(a.consumed_at) : "Not used yet"}
              </Field>
            )}
          </>
        )}
      </dl>
      {command && (
        <section aria-labelledby={`${a.id}-cmd`}>
          <h3 id={`${a.id}-cmd`}>Command</h3>
          {/* The exact command an approval is bound to, after the server's scrubbing; text only. */}
          <pre
            tabIndex={0}
            aria-label="Command"
            className="apr-args"
            data-testid="approval-command"
          >
            {command.length > ARG_CHARS ? command.slice(0, ARG_CHARS) : command}
          </pre>
          <p className="muted">Approving covers exactly this command, once.</p>
        </section>
      )}
      <section aria-labelledby={`${a.id}-args`}>
        <h3 id={`${a.id}-args`}>Arguments</h3>
        {a.content_withheld ? (
          <p className="muted" data-testid="content-unavailable">
            {a.state === "REQUESTED"
              ? "The content of this request could not be shown (it was withheld when the request was made), so it cannot be approved. You can deny it."
              : "The content of this request could not be shown: it was withheld when the request was made."}
          </p>
        ) : json ? (
          <>
            {/* Captured arguments are untrusted: rendered as text only, after the server's scrubbing. */}
            <pre tabIndex={0} aria-label="Arguments" className="apr-args">
              {json.length > ARG_CHARS ? json.slice(0, ARG_CHARS) : json}
            </pre>
            {a.redactions > 0 && (
              <p className="muted">
                {a.redactions} value{a.redactions === 1 ? " was" : "s were"} hidden before storage.
              </p>
            )}
          </>
        ) : (
          <p className="muted">None.</p>
        )}
      </section>
      {outcome?.kind === "ok" && (
        <p
          role="status"
          className="notice apr-flash"
          data-testid="decision-saved"
          ref={answer}
          tabIndex={-1}
        >
          {outcome.text}
        </p>
      )}
      {outcome?.kind === "error" && (
        <p
          role="alert"
          className="notice"
          data-state="denied"
          data-testid="decision-error"
          ref={answer}
          tabIndex={-1}
        >
          {decisionErrorText(
            outcome.error.code,
            outcome.error.status,
            outcome.error.details["state"],
          )}
        </p>
      )}
      {a.state === "REQUESTED" &&
        (canDecide ? (
          <DecideForm id={a.id} onOutcome={setOutcome} canApprove={!a.content_withheld} />
        ) : (
          <p className="muted apr-note" role="note">
            Your role can read this request but not decide it.
          </p>
        ))}
    </>
  );
}

function DecideForm({
  id,
  onOutcome,
  canApprove,
}: {
  id: string;
  canApprove: boolean;
  onOutcome: (
    o: { kind: "ok"; text: string } | { kind: "error"; error: ApiRequestError } | null,
  ) => void;
}) {
  const decide = useDecideApproval(id, (o) =>
    onOutcome(
      o.kind === "ok"
        ? {
            kind: "ok",
            text:
              o.state === "APPROVED" ? "You approved this request." : "You denied this request.",
          }
        : o.error instanceof ApiRequestError
          ? { kind: "error", error: o.error }
          : null,
    ),
  );
  const [comment, setComment] = useState("");
  const [confirming, setConfirming] = useState<"approve" | "deny" | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const field = useId();
  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);
  const tooLong = comment.length > MAX_COMMENT_CHARS;
  return (
    <form
      className="apr-decide"
      aria-label="Decide this request"
      onKeyDown={(e) => {
        // Escape first undoes the confirm step; only then does it close the panel.
        if (e.key === "Escape" && confirming) {
          e.preventDefault();
          setConfirming(null);
        }
      }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!confirming || tooLong) return;
        onOutcome(null);
        decide.mutate(
          { decision: confirming, comment: comment.trim() },
          { onSettled: () => setConfirming(null) },
        );
      }}
    >
      <p>
        <label htmlFor={field}>Comment (optional)</label>
        <textarea
          id={field}
          rows={3}
          value={comment}
          maxLength={MAX_COMMENT_CHARS + 200}
          aria-describedby={`${field}-n`}
          onChange={(e) => setComment(e.target.value)}
        />
        <span id={`${field}-n`} className="muted apr-count">
          {comment.length} / {MAX_COMMENT_CHARS}
          {tooLong ? " (too long)" : ""}
        </span>
      </p>
      {confirming ? (
        <div className="apr-confirm" role="group" aria-label="Confirm your decision">
          <p>
            {confirming === "approve"
              ? "Approve this request? It authorises this one action, once."
              : "Deny this request? The agent is told it was refused."}
          </p>
          <button
            ref={confirmRef}
            type="submit"
            className={confirming === "deny" ? "danger" : undefined}
            disabled={decide.isPending || tooLong}
          >
            {decide.isPending
              ? "Saving…"
              : confirming === "approve"
                ? "Confirm approve"
                : "Confirm deny"}
          </button>{" "}
          <button type="button" disabled={decide.isPending} onClick={() => setConfirming(null)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="apr-actions">
          <button
            type="button"
            onClick={() => setConfirming("approve")}
            disabled={tooLong || !canApprove}
          >
            Approve
          </button>{" "}
          <button type="button" onClick={() => setConfirming("deny")} disabled={tooLong}>
            Deny
          </button>
        </div>
      )}
    </form>
  );
}
