"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import { useSecuritySettings, useUpdateSecuritySettings } from "@/lib/admin";
import type { components } from "@/lib/api/schema";
import {
  CAPTURE_MODES,
  MAX_ALLOWLIST_ENTRIES,
  detectorName,
  normalizeHostPattern,
  securitySeverityMeta,
  type CaptureMode,
} from "@/lib/security";
import { SecuritySeverityMark } from "./SecuritySeverity";
import { settings as copy } from "@/copy/settings";
import { ActionError, Lede, StatusLine } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/security.css";

type Settings = components["schemas"]["SecuritySettingsOut"];

const same = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && [...a].sort().every((v, i) => v === [...b].sort()[i]);

/**
 * Per-project security settings: how much the server stores (capture mode), which detectors report, and which hosts an
 * agent may contact. Only people who may write projects reach it (the API answers 403 to others). What is shown after a
 * save is what the API stored, never a guess.
 */
export function SecuritySettings() {
  const ws = useWorkspace();
  const [picked, setPicked] = useState(ws.projects[0]?.id ?? null);
  // Kept here: a save replaces the settings (and so remounts the form), the confirmation must outlive that.
  const [saved, setSaved] = useState(false);
  const pickerId = useId();
  const q = useSecuritySettings(picked);
  return (
    <div className="sec-settings">
      <Lede>
        {copy.security.ledeLead}
        <AuditLink />.
      </Lede>
      {ws.projects.length === 0 ? (
        <Empty title="No projects yet" motif="none">
          Settings appear once an agent reports a project.
        </Empty>
      ) : (
        <>
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
          {q.isPending ? (
            <Loading label="Loading security settings" />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          ) : (
            <SettingsForm
              key={`${q.data.project_id}|${q.data.updated_at}`}
              settings={q.data}
              saved={saved}
              setSaved={setSaved}
            />
          )}
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

function SettingsForm({
  settings,
  saved,
  setSaved,
}: {
  settings: Settings;
  saved: boolean;
  setSaved: (v: boolean) => void;
}) {
  const save = useUpdateSecuritySettings(settings.project_id);
  const [mode, setMode] = useState<CaptureMode>(settings.capture_mode);
  const [muted, setMuted] = useState<string[]>(settings.muted_detectors);
  const [allow, setAllow] = useState<string[]>(settings.destination_allowlist);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const name = `mode-${useId()}`;

  const dirty =
    mode !== settings.capture_mode ||
    !same(muted, settings.muted_detectors) ||
    !same(allow, settings.destination_allowlist);

  const add = () => {
    const r = normalizeHostPattern(draft);
    if (!r.ok) return setDraftError(r.message);
    if (allow.length >= MAX_ALLOWLIST_ENTRIES)
      return setDraftError(`At most ${MAX_ALLOWLIST_ENTRIES} allowlist entries.`);
    setDraftError(null);
    setSaved(false);
    if (!allow.includes(r.host)) setAllow([...allow, r.host].sort());
    setDraft("");
  };

  const submit = () => {
    // Partial update: only what changed goes over the wire.
    const body: components["schemas"]["SecuritySettingsIn"] = {};
    if (mode !== settings.capture_mode) body.capture_mode = mode;
    if (!same(muted, settings.muted_detectors)) body.muted_detectors = muted;
    if (!same(allow, settings.destination_allowlist)) body.destination_allowlist = allow;
    setSaved(false);
    save.mutate(body, { onSuccess: () => setSaved(true) });
  };

  const apiMessage =
    save.error instanceof ApiRequestError && save.error.code === "SETTINGS_INVALID"
      ? save.error.message
      : null;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <fieldset>
        <legend>Capture mode</legend>
        {CAPTURE_MODES.map((m) => (
          <label key={m.value} className="sec-radio">
            <input
              type="radio"
              name={name}
              value={m.value}
              checked={mode === m.value}
              onChange={() => {
                setMode(m.value);
                setSaved(false);
              }}
            />
            <span>{m.label}</span>
            <small>{m.hint}</small>
          </label>
        ))}
        <p className="muted sec-note">
          A change applies to new events only. Events already stored are not scrubbed or removed.
        </p>
      </fieldset>

      <fieldset>
        <legend>Detectors</legend>
        <p className="muted sec-note">
          Muting a detector or changing the allowed hosts re-analyses this project&apos;s recent
          runs in the background; recent runs first.
        </p>
        {settings.detectors.map((d) => {
          const on = !muted.includes(d.id);
          const sev = d.severities.map((s) => securitySeverityMeta(s).label).join(" or ");
          return (
            <div key={d.id} className="sec-detector-row" data-testid="detector-row">
              <div>
                <strong>{d.title}</strong>{" "}
                {d.severities.length > 0 && (
                  <SecuritySeverityMark severity={d.severities.at(-1)!} />
                )}
                <span className="sr-only"> ({sev})</span>
                <p>{d.description}</p>
              </div>
              <label>
                <input
                  type="checkbox"
                  checked={on}
                  aria-label={`Report ${detectorName(d.id).toLowerCase()}`}
                  onChange={() => {
                    setSaved(false);
                    setMuted(on ? [...muted, d.id] : muted.filter((x) => x !== d.id));
                  }}
                />{" "}
                {on ? "On" : "Muted"}
              </label>
            </div>
          );
        })}
      </fieldset>

      <fieldset>
        <legend>Allowed hosts</legend>
        <p className="muted sec-note">
          Hosts the agents of this project may contact, such as <code>api.example.com</code> or{" "}
          <code>*.example.com</code> (any subdomain; the bare domain needs its own entry). With no
          allowlist nothing is reported for destinations. localhost is always allowed.
        </p>
        {allow.length === 0 ? (
          <p className="muted" data-testid="allowlist-empty">
            No hosts yet.
          </p>
        ) : (
          <ul className="sec-allow" aria-label="Allowed hosts">
            {allow.map((h) => (
              <li key={h}>
                {h}
                <button
                  type="button"
                  className="danger"
                  aria-label={`Remove ${h}`}
                  onClick={() => {
                    setSaved(false);
                    setAllow(allow.filter((x) => x !== h));
                  }}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="sec-add">
          <label className="field">
            Add a host
            <input
              value={draft}
              maxLength={120}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={draftError ? true : undefined}
              aria-describedby={draftError ? "allow-error" : undefined}
              onChange={(e) => {
                setDraft(e.target.value);
                setDraftError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add();
                }
              }}
            />
          </label>
          <button type="button" onClick={add}>
            Add
          </button>
        </div>
        {draftError && (
          <span id="allow-error" className="field-error" role="alert">
            {draftError}
          </span>
        )}
      </fieldset>

      <button type="submit" className="primary" disabled={!dirty || save.isPending}>
        {save.isPending ? copy.saving : "Save changes"}
      </button>
      <StatusLine testId="save-status">
        {saved && !dirty
          ? "Saved. Changes apply to new events now. Existing runs are re-analysed in the background; recent runs first."
          : ""}
      </StatusLine>
      {apiMessage ? (
        <p role="alert" className="notice" data-state="error">
          {apiMessage}
        </p>
      ) : (
        <ActionError error={save.error} />
      )}
    </form>
  );
}
