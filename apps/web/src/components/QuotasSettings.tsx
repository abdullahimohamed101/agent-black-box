"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import { useProjectQuotas, useUpdateProjectQuotas } from "@/lib/admin";
import type { components } from "@/lib/api/schema";
import {
  QUOTA_ORDER,
  barPercent,
  formatQuantity,
  inputValue,
  parseOverride,
  quotaMeta,
  quotaStatus,
  usageSentence,
  type ProjectQuotas,
  type QuotaName,
  type QuotaRow,
} from "@/lib/quotas";
import { settings } from "@/copy/settings";
import { ActionError, Lede, ReadOnly, Section, StatusLine, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/quotas.css";

/**
 * What a project may store, and how much of it is used (Phase 19a, ADR-154; settings pattern, design pass 2). Everyone who
 * can read projects sees the usage; owners and admins can override the limits in the same row. At a limit new items are
 * refused loudly, existing data keeps working, and the page says so in words. What is shown after a save is what the API
 * stored, never a guess.
 */
export function QuotasSettings() {
  const ws = useWorkspace();
  const [picked, setPicked] = useState(ws.projects[0]?.id ?? null);
  const [saved, setSaved] = useState(false);
  const pickerId = useId();
  const q = useProjectQuotas(picked);
  return (
    <div className="quotas">
      <Lede>
        {settings.quotas.ledeLead}
        <AuditLink />.
      </Lede>
      {ws.projects.length === 0 ? (
        <Empty title="No projects yet">Quotas appear once an agent reports a project.</Empty>
      ) : (
        <Section
          title={settings.quotas.usageTitle}
          action={
            <p className="st-scope">
              <label htmlFor={pickerId}>Project </label>
              <select
                id={pickerId}
                value={picked ?? ""}
                onChange={(e) => {
                  setPicked(e.target.value);
                  setSaved(false);
                }}
              >
                {ws.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </p>
          }
        >
          {q.isPending ? (
            <Loading label="Loading quotas" />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          ) : (
            <Usage
              key={`${q.data.project_id}|${q.data.updated_at}`}
              data={q.data}
              saved={saved}
              setSaved={setSaved}
            />
          )}
        </Section>
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

function Usage({
  data,
  saved,
  setSaved,
}: {
  data: ProjectQuotas;
  saved: boolean;
  setSaved: (v: boolean) => void;
}) {
  const canEdit = useCan("project.write");
  const rows = QUOTA_ORDER.map((name) => data.quotas.find((r) => r.quota === name)).filter(
    (r): r is QuotaRow => r !== undefined,
  );
  const save = useUpdateProjectQuotas(data.project_id);
  const stored = (name: QuotaName): number | null => {
    const r = rows.find((x) => x.quota === name);
    return r?.overridden ? r.limit : null;
  };
  const [text, setText] = useState<Record<QuotaName, string>>(() => ({
    agents: inputValue("agents", stored("agents")),
    runs_per_day: inputValue("runs_per_day", stored("runs_per_day")),
    events_per_day: inputValue("events_per_day", stored("events_per_day")),
    artifact_bytes: inputValue("artifact_bytes", stored("artifact_bytes")),
  }));
  const [errors, setErrors] = useState<Partial<Record<QuotaName, string>>>({});
  const base = useId();

  const parsed = QUOTA_ORDER.map((n) => [n, parseOverride(n, text[n])] as const);
  const changed = parsed.filter(([n, p]) => p.ok && p.value !== stored(n));
  const dirty = changed.length > 0 || parsed.some(([, p]) => !p.ok);

  const submit = () => {
    const next: Partial<Record<QuotaName, string>> = {};
    for (const [n, p] of parsed) if (!p.ok) next[n] = p.message;
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    // Partial update: only what changed goes over the wire; an emptied field sends null (back to the default).
    const body: components["schemas"]["QuotaOverridesIn"] = {};
    for (const [n, p] of changed) if (p.ok) body[n] = p.value;
    setSaved(false);
    save.mutate(body, { onSuccess: () => setSaved(true) });
  };

  const apiMessage =
    save.error instanceof ApiRequestError && save.error.code === "QUOTA_SETTINGS_INVALID"
      ? save.error.message
      : null;

  const list = (
    <ul className="quota-list" aria-label="Quota usage">
      {rows.map((row) => {
        const name = row.quota;
        const meta = quotaMeta(name);
        const id = `${base}-${name}`;
        const error = errors[name];
        return (
          <QuotaItem
            key={name}
            row={row}
            resetsAt={data.resets_at}
            field={
              canEdit && meta ? (
                <div className="quota-field">
                  <input
                    id={id}
                    aria-label={`${meta.label}${meta.unit === "bytes" ? " (MiB)" : ""}`}
                    inputMode="numeric"
                    autoComplete="off"
                    spellCheck={false}
                    value={text[name]}
                    placeholder={`Default ${formatQuantity(name, row.default)}`}
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? `${id}-error` : undefined}
                    onChange={(e) => {
                      setText({ ...text, [name]: e.target.value });
                      setErrors({ ...errors, [name]: undefined });
                      setSaved(false);
                    }}
                  />
                  {meta.unit === "bytes" && <span className="quota-unit">MiB</span>}
                  {error && (
                    <span id={`${id}-error`} className="field-error" role="alert">
                      {error}
                    </span>
                  )}
                </div>
              ) : null
            }
          />
        );
      })}
    </ul>
  );

  return (
    <>
      {!data.enforced && (
        <p className="notice" data-state="info" role="note">
          Quotas are switched off on this installation (<code>ABB_QUOTAS_ENABLED=false</code>):
          nothing is refused and the figures below are not being updated.
        </p>
      )}
      {canEdit ? (
        <form
          className="quota-form"
          aria-label="Quota limits"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {list}
          <p className="help quota-note">
            Leave a field empty to use the default. Lowering a limit below current use refuses new
            items only; nothing already stored is touched. A change applies to the next request.
            Daily figures count the UTC day <time dateTime={data.day}>{data.day}</time> and start
            again at {when(data.resets_at)}.
          </p>
          <button type="submit" className="primary" disabled={!dirty || save.isPending}>
            {save.isPending ? settings.saving : "Save limits"}
          </button>
          <StatusLine testId="save-status">
            {saved && !dirty ? "Saved. The new limits apply to the next request." : ""}
          </StatusLine>
          {apiMessage ? (
            <p role="alert" className="notice" data-state="error">
              {apiMessage}
            </p>
          ) : (
            <ActionError error={save.error} />
          )}
        </form>
      ) : (
        <>
          {list}
          <p className="help quota-note">
            Daily figures count the UTC day <time dateTime={data.day}>{data.day}</time> and start
            again at {when(data.resets_at)}.
          </p>
          <ReadOnly testId="quota-readonly">Owners and admins can change these limits.</ReadOnly>
        </>
      )}
    </>
  );
}

function QuotaItem({
  row,
  resetsAt,
  field,
}: {
  row: QuotaRow;
  resetsAt: string;
  field: React.ReactNode;
}) {
  const meta = quotaMeta(row.quota);
  const status = quotaStatus(row.status);
  const label = meta?.label ?? row.quota;
  return (
    <li
      className="quota-item"
      data-testid="quota-item"
      data-quota={row.quota}
      data-status={row.status}
    >
      <div className="quota-name">
        <h3>{label}</h3>
        <p className="muted quota-meta">
          {row.overridden ? (
            <>
              Overridden for this project. The default is {formatQuantity(row.quota, row.default)}.
            </>
          ) : (
            <>Default limit.</>
          )}
        </p>
      </div>
      <p className="quota-figures">
        <strong>{usageSentence(row)}</strong> ({row.percent}%)
      </p>
      <div className="quota-bar" aria-hidden="true">
        <span style={{ width: `${barPercent(row.percent)}%` }} data-status={row.status} />
      </div>
      <span className="quota-status" data-status={row.status}>
        <span className="quota-glyph" aria-hidden="true">
          {status.glyph}
        </span>
        {status.word}
      </span>
      {field}
      {row.status === "at_limit" && (
        <p className="quota-refusing" data-testid="quota-refusing">
          {meta?.refusal ?? "New items are refused."}
          {row.period === "day" && <> Starts again at {when(resetsAt)}.</>}
        </p>
      )}
    </li>
  );
}
