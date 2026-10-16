"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import {
  useRetentionPolicy,
  useRetentionPreview,
  useRetentionReports,
  useUpdateRetention,
} from "@/lib/admin";
import {
  MODE_BANNER,
  MODE_WORD,
  REPORT_TIER_LABEL,
  TIER_META,
  TIER_ORDER,
  changedBody,
  describeDays,
  cancelBody,
  describeTier,
  draftsOf,
  floorOf,
  graceSentence,
  isDirty,
  pendingSentence,
  previewParams,
  previewSentence,
  previewTiming,
  previewSummary,
  propose,
  reportCounts,
  retentionError,
  shortened,
  tierOf,
  type Choice,
  type Drafts,
  type RetentionMode,
  type RetentionPolicy,
  type RetentionPreview,
  type TierName,
} from "@/lib/retention";
import { settings } from "@/copy/settings";
import { Lede, ReadOnly, Section, StatusLine, when } from "./SettingsKit";
import { ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/retention.css";

/**
 * How long the workspace keeps its data (Phase 19b, ADR-159/160). Everyone who can read the workspace sees the periods,
 * what the job did last and whether it is deleting at all; owners and admins edit. A save that shortens a period asks
 * first, because deletion is permanent once the installation enforces. What is shown after a save is what the API stored.
 */
export function RetentionSettings() {
  const ws = useWorkspace();
  const policy = useRetentionPolicy(ws.workspace.id);
  // Held here, not in the form: a save changes `updated_at`, which remounts the form from what the API stored.
  const [saved, setSaved] = useState(false);
  return (
    <div className="retention">
      <Lede>
        {settings.retention.ledeLead}
        <AuditLink />.
      </Lede>
      {policy.isPending ? (
        <Loading label="Loading the retention policy" />
      ) : policy.isError ? (
        <ErrorState error={policy.error} onRetry={() => void policy.refetch()} />
      ) : (
        <>
          <ModeBanner mode={policy.data.mode} />
          <Section title="Periods">
            <Policy
              key={policy.data.updated_at ?? "default"}
              policy={policy.data}
              saved={saved}
              setSaved={setSaved}
            />
          </Section>
          <LastPasses />
        </>
      )}
    </div>
  );
}

function AuditLink() {
  const ws = useWorkspace();
  const canAudit = useCan("audit.read");
  return canAudit ? (
    <Link href={`/w/${ws.workspace.slug}/settings/audit`}>audit log</Link>
  ) : (
    <>audit log</>
  );
}

/** Whether anything is being deleted: a sentence, a glyph and the mode in words (never colour alone, never red). */
function ModeBanner({ mode }: { mode: RetentionMode }) {
  const b = MODE_BANNER[mode];
  return (
    <p className="retention-mode" role="note" data-testid="retention-mode" data-mode={mode}>
      <span className="retention-glyph" aria-hidden="true">
        {b.glyph}
      </span>
      <span>
        <strong>{b.title}</strong>
        <span className="muted retention-mode-detail"> {b.detail}</span>
      </span>
    </p>
  );
}

const CHOICES: { value: Choice; label: string }[] = [
  { value: "default", label: "Use the installation default" },
  { value: "days", label: "Keep for a number of days" },
  { value: "forever", label: "Keep forever" },
];

function Policy({
  policy,
  saved,
  setSaved,
}: {
  policy: RetentionPolicy;
  saved: boolean;
  setSaved: (v: boolean) => void;
}) {
  const ws = useWorkspace();
  const canEdit = useCan("retention.write");
  const canEditAudit = useCan("retention.audit.write");
  const [drafts, setDrafts] = useState<Drafts>(() => draftsOf(policy));
  const [shown, setShown] = useState(false); // validation messages appear after the first try
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<{ key: string; data: RetentionPreview } | null>(null);
  const save = useUpdateRetention(ws.workspace.id);
  const previewer = useRetentionPreview(ws.workspace.id);
  const base = useId();
  const confirmRef = useRef<HTMLDivElement>(null);

  const proposal = propose(policy, drafts);
  const body = changedBody(policy, drafts);
  const dirty = isDirty(policy, drafts);
  const problems = proposal.ok ? {} : proposal.errors;
  const shorter = proposal.ok ? shortened(policy, proposal.effective) : [];
  const key = proposal.ok ? JSON.stringify(previewParams(proposal.effective)) : "";

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const edit = (name: TierName, next: Partial<Drafts[TierName]>) => {
    setDrafts({ ...drafts, [name]: { ...drafts[name], ...next } });
    setSaved(false);
    setConfirming(false);
    save.reset();
    previewer.reset();
  };

  const runPreview = () => {
    setShown(true);
    if (!proposal.ok) return;
    previewer.mutate(previewParams(proposal.effective), {
      onSuccess: (data) => setPreview({ key, data }),
    });
  };

  const submit = () => {
    setShown(true);
    if (!proposal.ok || !dirty) return;
    if (shorter.length > 0 && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    setSaved(false);
    save.mutate(body, { onSuccess: () => setSaved(true) });
  };

  const current = preview && preview.key === key ? preview.data : null;

  return (
    <form
      className="retention-form"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      aria-label="Retention periods"
    >
      <ul className="retention-list">
        {TIER_ORDER.map((name) => {
          const tier = tierOf(policy, name);
          if (!tier) return null;
          const meta = TIER_META[name];
          const id = `${base}-${name}`;
          const draft = drafts[name];
          const error = shown ? problems[name] : undefined;
          const ownerOnly = name === "audit" && !canEditAudit;
          const editable = canEdit && !ownerOnly;
          const waiting = pendingSentence(tier);
          const cancel = cancelBody(tier);
          return (
            <li
              key={name}
              className="retention-tier"
              data-testid="retention-tier"
              data-tier={name}
              data-source={tier.source}
            >
              <div className="retention-head">
                <h3 id={`${id}-h`}>{meta.label}</h3>
                <span className="retention-value" data-testid="tier-value">
                  {describeTier(tier)}
                </span>
              </div>
              <p className="muted retention-removes">{meta.removes}</p>
              {waiting && (
                <p className="retention-pending" data-testid="tier-pending">
                  <span aria-hidden="true">❙❙ </span>
                  {waiting}{" "}
                  {editable && cancel && (
                    <button
                      type="button"
                      disabled={save.isPending}
                      onClick={() => {
                        setSaved(false);
                        save.mutate(cancel);
                      }}
                    >
                      Cancel this shortening
                    </button>
                  )}
                </p>
              )}
              {canEdit && ownerOnly && (
                <p className="muted retention-note" data-testid="audit-owner-only">
                  Only an owner can change how long the audit log is kept: it is the record of who
                  changed these periods. Its shortest period is {floorOf(policy, "audit")} days.
                </p>
              )}
              {editable && (
                <div className="retention-field">
                  <label htmlFor={`${id}-choice`}>{meta.label} period</label>
                  <div className="retention-inputs">
                    <select
                      id={`${id}-choice`}
                      value={draft.choice}
                      aria-describedby={error ? `${id}-error` : undefined}
                      onChange={(e) => edit(name, { choice: e.target.value as Choice })}
                    >
                      {CHOICES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.value === "default"
                            ? `${c.label} (${describeDays(tier.default_days)})`
                            : c.label}
                        </option>
                      ))}
                    </select>
                    {draft.choice === "days" && (
                      <span className="retention-days">
                        <input
                          id={`${id}-days`}
                          aria-label={`${meta.label} days`}
                          inputMode="numeric"
                          autoComplete="off"
                          spellCheck={false}
                          value={draft.days}
                          aria-invalid={error ? true : undefined}
                          aria-describedby={error ? `${id}-error` : undefined}
                          onChange={(e) => edit(name, { days: e.target.value })}
                        />{" "}
                        days
                      </span>
                    )}
                  </div>
                  {error && (
                    <span id={`${id}-error`} className="field-error" role="alert">
                      {error}
                    </span>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted retention-note">
        The run record is deleted last, so it is kept at least as long as trace detail, and trace
        detail at least as long as artifacts. The shortest period is {policy.floor_days} days (
        {policy.audit_floor_days} for the audit log).
      </p>
      {!canEdit ? (
        <ReadOnly testId="retention-readonly">Owners and admins can change these periods.</ReadOnly>
      ) : (
        <>
          <div className="retention-actions">
            <button
              type="button"
              onClick={runPreview}
              disabled={previewer.isPending || (shown && !proposal.ok)}
            >
              {previewer.isPending ? "Measuring…" : "Preview what this would remove"}
            </button>
            <button type="submit" className="primary" disabled={!dirty || save.isPending}>
              {save.isPending ? "Saving…" : "Save periods"}
            </button>
          </div>
          {previewer.isError && <PreviewProblem error={previewer.error} onRetry={runPreview} />}
          {current && <PreviewResult data={current} mode={policy.mode} />}
          {confirming && (
            <div
              className="notice retention-confirm"
              role="group"
              aria-labelledby={`${base}-confirm-h`}
              tabIndex={-1}
              ref={confirmRef}
              data-testid="retention-confirm"
            >
              <p id={`${base}-confirm-h`}>
                <strong>This shortens how long data is kept.</strong>
              </p>
              <ul>
                {shorter.map((name) => {
                  const tier = tierOf(policy, name)!;
                  const next = proposal.ok ? proposal.effective[name] : null;
                  return (
                    <li key={name}>
                      {TIER_META[name].label}: from {describeDays(tier.forever ? null : tier.days)}{" "}
                      to {describeDays(next)}
                    </li>
                  );
                })}
              </ul>
              <p data-testid="grace-note">{graceSentence(policy)}</p>
              <p>
                {policy.mode === "enforce"
                  ? "The retention job is enforcing, so data older than the new period will be deleted permanently and cannot be restored."
                  : "When the retention job is enforcing, data older than the new period is deleted permanently and cannot be restored."}
              </p>
              <div className="retention-actions">
                <button type="submit" className="primary">
                  Save and shorten
                </button>
                <button type="button" onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          <StatusLine testId="save-status">
            {saved && !dirty
              ? policy.tiers.some((t) => t.pending)
                ? "Saved. A shorter period takes effect after its waiting time; until then the longer one still applies."
                : "Saved. The retention job applies the new periods on its next pass."
              : ""}
          </StatusLine>
          {save.isError && (
            <p role="alert" className="notice" data-state="error">
              {retentionError(save.error).text}
            </p>
          )}
        </>
      )}
    </form>
  );
}

function PreviewProblem({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const e = retentionError(error);
  return (
    <p role="alert" className="notice" data-state="error" data-testid="preview-error">
      {e.text}{" "}
      {e.retry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </p>
  );
}

function PreviewResult({ data, mode }: { data: RetentionPreview; mode: RetentionMode }) {
  return (
    <section className="retention-preview" aria-label="Preview" data-testid="retention-preview">
      <p className="retention-preview-summary">
        <strong>{previewSummary(data.tiers)}</strong>
      </p>
      <ul>
        {data.tiers.map((t) => (
          <li key={t.tier} data-tier={t.tier}>
            <strong>{TIER_META[t.tier].label}.</strong> {previewSentence(t)} {previewTiming(t)}
          </li>
        ))}
      </ul>
      <p className="muted retention-note">
        {mode === "dry_run"
          ? "The retention job is in dry-run mode, so nothing is deleted yet. "
          : mode === "enforce"
            ? "The retention job is enforcing: this is what its next passes would delete. "
            : "Retention is off, so nothing is deleted. "}
        Each tier is measured on its own, so the figures overlap and do not add up. Nothing was
        saved.
      </p>
    </section>
  );
}

function LastPasses() {
  const ws = useWorkspace();
  const reports = useRetentionReports(ws.workspace.id);
  return (
    <Section title="Last passes" testId="retention-passes">
      {reports.isPending ? (
        <Loading label="Loading the last passes" />
      ) : reports.isError ? (
        <ErrorState error={reports.error} onRetry={() => void reports.refetch()} />
      ) : reports.data.length === 0 ? (
        <p className="muted" data-testid="no-passes">
          No passes yet. The retention job reports here after it has run once for this workspace.
        </p>
      ) : (
        <ul className="retention-pass-list">
          {reports.data.map((r) => (
            <li key={r.id} data-testid="retention-pass" data-mode={r.mode}>
              <p className="retention-pass-head">
                <time dateTime={r.finished_at}>{when(r.finished_at)}</time>
                {" · "}
                <span>{REPORT_TIER_LABEL[r.tier]}</span>
                {" · "}
                <span className="retention-pass-mode">
                  <span aria-hidden="true">{r.mode === "dry_run" ? "◌" : "●"} </span>
                  {MODE_WORD[r.mode]}
                </span>
              </p>
              <p className="muted">
                {reportCounts(r)}
                {r.truncated && (
                  <>
                    {" "}
                    <span className="retention-truncated">
                      Stopped early at a limit, so these figures are a lower bound.
                    </span>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
