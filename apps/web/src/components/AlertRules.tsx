"use client";

import { useState } from "react";
import { ApiRequestError } from "@/lib/api/client";
import {
  FILTERS_FOR,
  RUN_STATUSES,
  RUN_STATUS_WORD,
  SEVERITIES,
  TRIGGERS,
  TRIGGER_WORDS,
  TYPE_WORD,
  buildRuleBody,
  cooldownWords,
  destinationHealth,
  draftFromRule,
  filterWords,
  newDraft,
  previewWords,
  problemFrom,
  triggerShort,
  type AlertDestination,
  type AlertRule,
  type AlertTrigger,
  type RuleDraft,
} from "@/lib/alerts";
import {
  useAlertDestinations,
  useAlertRules,
  useCreateRule,
  useDeleteRule,
  useRulePreview,
  useUpdateRule,
} from "@/lib/alertsApi";
import { AlertProblem } from "./AlertMarks";
import { FieldError, Forbidden, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import "../app/alerts.css";

export type Intent = { kind: "destination" | "rule" | "starter" } | null;

const SEVERITY_LABEL: Record<string, string> = {
  info: "Info",
  warning: "Warning",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

/** "With its saved settings this rule would have fired N times in the last 7 days." Counts only. */
export function PreviewLine({ ruleId }: { ruleId: string }) {
  const q = useRulePreview(ruleId, 7);
  if (q.isPending)
    return (
      <p className="muted" role="status">
        Checking the last 7 days…
      </p>
    );
  if (q.isError) {
    const e = q.error instanceof ApiRequestError ? q.error : null;
    const text =
      e?.code === "ALERT_PREVIEW_TIMEOUT"
        ? "The preview took too long to work out. The rule is not affected."
        : e?.code === "RATE_LIMITED"
          ? "Too many previews in a short time. Try again in a moment."
          : "The preview is not available right now. The rule is not affected.";
    return (
      <p className="muted" role="status" data-testid="preview">
        {text}{" "}
        <button type="button" onClick={() => void q.refetch()}>
          Try again
        </button>
      </p>
    );
  }
  return (
    <p role="status" data-testid="preview">
      With its saved settings this rule {previewWords(q.data.count, q.data.days)}.{" "}
      <span className="muted">
        Counted from recorded data, before any cooldown. Nothing is sent.
      </span>
    </p>
  );
}

export function AlertRules({
  go,
  intent,
}: {
  go: (tab: "destinations", intent: Intent) => void;
  intent: Intent;
}) {
  const ws = useWorkspace();
  const canWrite = useCan("alert.write");
  const canDest = useCan("alert.destination.read");
  const rules = useAlertRules(true);
  const dests = useAlertDestinations(canDest);
  const update = useUpdateRule();
  const remove = useDeleteRule();
  const [editing, setEditing] = useState<string | "new" | null>(
    intent?.kind === "rule" || intent?.kind === "starter" ? "new" : null,
  );
  const [starter, setStarter] = useState(intent?.kind === "starter");
  const [saved, setSaved] = useState<{ id: string; name: string } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const projectName = (id: string) => ws.projects.find((p) => p.id === id)?.name ?? "a project";

  if (rules.isPending) return <Loading label="Loading rules" />;
  if (rules.isError) return <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />;
  const items = rules.data;
  const destinations: AlertDestination[] = dests.data?.items ?? [];
  const noDestinations = canDest && dests.isSuccess && destinations.length === 0;
  const editRule = editing && editing !== "new" ? items.find((r) => r.id === editing) : undefined;

  const done = () => {
    setEditing(null);
    setStarter(false);
  };

  return (
    <section aria-label="Alert rules">
      {canWrite && editing === null && items.length > 0 && (
        <p className="al-add">
          <button
            type="button"
            className="primary"
            onClick={() =>
              noDestinations ? go("destinations", { kind: "destination" }) : setEditing("new")
            }
          >
            {noDestinations ? "Add a destination first" : "Add a rule"}
          </button>
        </p>
      )}
      {saved && (
        <div role="status" className="al-saved" data-testid="rule-saved">
          <p>
            Saved <strong>{saved.name}</strong>.
          </p>
          <PreviewLine ruleId={saved.id} />
          <button type="button" onClick={() => setSaved(null)}>
            Dismiss
          </button>
        </div>
      )}
      {canWrite && editing !== null && (
        <>
          {dests.isPending ? (
            <Loading label="Loading destinations" />
          ) : noDestinations ? (
            <Empty title="A rule needs somewhere to send alerts">
              Add a destination first, then come back to this rule.
            </Empty>
          ) : (
            <RuleForm
              key={`${editing}|${starter}`}
              rule={editRule}
              initial={starter ? newDraft({ name: "Failed runs" }) : undefined}
              destinations={destinations}
              onCancel={done}
              onSaved={(r) => {
                done();
                setSaved({ id: r.id, name: r.name });
              }}
            />
          )}
        </>
      )}
      {items.length === 0 ? (
        <div className="al-empty" data-testid="alerts-empty">
          {!canWrite ? (
            <Empty title="No alert rules yet">
              Owners and admins can add rules that send an alert when something needs attention.
            </Empty>
          ) : noDestinations ? (
            <Empty
              title="Add a destination, then a rule"
              action={
                <button
                  type="button"
                  className="primary"
                  onClick={() => go("destinations", { kind: "destination" })}
                >
                  Add a destination
                </button>
              }
            >
              A destination is where alerts go: a webhook, a Slack channel or an email address. A
              rule says what to watch for.
            </Empty>
          ) : (
            editing === null && (
              <Empty
                title="Now add a rule"
                action={
                  <>
                    <button
                      type="button"
                      className="primary"
                      onClick={() => {
                        setStarter(true);
                        setEditing("new");
                      }}
                    >
                      Start with &ldquo;a run fails&rdquo;
                    </button>{" "}
                    <button type="button" onClick={() => setEditing("new")}>
                      Add a rule
                    </button>
                  </>
                }
              >
                A rule says what to watch for and which destinations to tell.
              </Empty>
            )
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="al-table">
            <caption className="sr-only">Alert rules</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">When</th>
                <th scope="col">Only if</th>
                <th scope="col">Sends to</th>
                <th scope="col">On</th>
                <th scope="col">Last fired</th>
                {canWrite && <th scope="col">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} data-testid="rule-row" data-enabled={r.enabled}>
                  <td className="al-name">{r.name}</td>
                  <td>{triggerShort(r.trigger)}</td>
                  <td>
                    <ul className="al-words">
                      {filterWords(r, projectName).map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                    <span className="muted">{cooldownWords(r.cooldown_seconds)}</span>
                  </td>
                  <td>
                    {r.destinations.length > 0 ? (
                      <ul className="al-words">
                        {r.destinations.map((d) => (
                          <li key={d.id}>
                            {d.name} <span className="muted">({TYPE_WORD[d.type]})</span>
                            {d.state !== "active" && (
                              <span className="al-flag">
                                {" "}
                                {destinationHealth({
                                  ...d,
                                  consecutive_failures: 0,
                                  paused_reason: null,
                                }).word.toLowerCase()}
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="muted">
                        {r.destination_count} destination{r.destination_count === 1 ? "" : "s"}
                      </span>
                    )}
                  </td>
                  <td>
                    {canWrite ? (
                      <button
                        type="button"
                        role="switch"
                        aria-checked={r.enabled}
                        aria-label={`${r.name} is ${r.enabled ? "on" : "off"}`}
                        className="toggle"
                        disabled={update.isPending}
                        onClick={() => update.mutate({ id: r.id, body: { enabled: !r.enabled } })}
                      >
                        {r.enabled ? "On" : "Off"}
                      </button>
                    ) : r.enabled ? (
                      "On"
                    ) : (
                      "Off"
                    )}
                  </td>
                  <td>{r.last_fired_at ? when(r.last_fired_at) : "Never"}</td>
                  {canWrite && (
                    <td className="al-row-actions">
                      {confirm === r.id ? (
                        <>
                          <span>Delete this rule?</span>{" "}
                          <button
                            type="button"
                            className="danger"
                            onClick={() =>
                              remove.mutate(r.id, { onSettled: () => setConfirm(null) })
                            }
                          >
                            Delete {r.name}
                          </button>{" "}
                          <button type="button" onClick={() => setConfirm(null)}>
                            Keep
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            aria-label={`Edit ${r.name}`}
                            onClick={() => {
                              setStarter(false);
                              setEditing(r.id);
                            }}
                          >
                            Edit
                          </button>{" "}
                          <button
                            type="button"
                            className="danger"
                            aria-label={`Delete ${r.name}`}
                            onClick={() => setConfirm(r.id)}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AlertProblem error={update.error ?? remove.error} kind="rule" />
      {!canWrite && items.length > 0 && (
        <Forbidden what="changing alert rules or seeing where they send" />
      )}
    </section>
  );
}

function RuleForm({
  rule,
  initial,
  destinations,
  onCancel,
  onSaved,
}: {
  rule?: AlertRule;
  initial?: RuleDraft;
  destinations: AlertDestination[];
  onCancel: () => void;
  onSaved: (r: AlertRule) => void;
}) {
  const ws = useWorkspace();
  const create = useCreateRule();
  const update = useUpdateRule();
  const [d, setD] = useState<RuleDraft>(() =>
    rule ? draftFromRule(rule) : (initial ?? newDraft()),
  );
  const [local, setLocal] = useState<Record<string, string>>({});
  const mutation = rule ? update : create;
  const server = problemFrom(mutation.error, "rule").fields;
  const err = { ...server, ...local };
  const use = FILTERS_FOR[d.trigger];
  const set = <K extends keyof RuleDraft>(k: K, v: RuleDraft[K]) => {
    setD((cur) => ({ ...cur, [k]: v }));
    setLocal({});
    mutation.reset();
  };
  const toggle = <T,>(list: T[], v: T): T[] =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const r = buildRuleBody(d);
    if (!r.ok) return setLocal(r.errors);
    setLocal({});
    if (rule) {
      const { trigger: _t, ...patch } = r.body;
      void _t;
      update.mutate({ id: rule.id, body: patch }, { onSuccess: (x) => onSaved(x) });
    } else create.mutate(r.body, { onSuccess: (x) => onSaved(x) });
  };

  return (
    <form className="al-form" onSubmit={submit} aria-label={rule ? "Edit rule" : "New rule"}>
      <h2>{rule ? `Edit ${rule.name}` : "New rule"}</h2>
      <label className="field">
        Tell me when
        <select
          value={d.trigger}
          disabled={!!rule}
          onChange={(e) => set("trigger", e.target.value as AlertTrigger)}
        >
          {TRIGGERS.map((t) => (
            <option key={t} value={t}>
              {TRIGGER_WORDS[t].when.replace(/^./, (c) => c.toUpperCase())}
            </option>
          ))}
        </select>
        {rule && <span className="muted">The trigger cannot change. Make a new rule instead.</span>}
      </label>
      <label className="field">
        Project
        <select value={d.projectId} onChange={(e) => set("projectId", e.target.value)}>
          <option value="">Any project</option>
          {ws.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <FieldError message={err.project_id} />
      </label>
      {use.agent && (
        <label className="field">
          Agent (optional)
          <input
            value={d.agent}
            maxLength={128}
            placeholder="agent name, for every agent leave empty"
            autoComplete="off"
            onChange={(e) => set("agent", e.target.value)}
          />
          <FieldError message={err.agent} />
        </label>
      )}
      {use.severity && (
        <label className="field">
          Severity or worse
          <select
            value={d.minSeverity}
            onChange={(e) => set("minSeverity", e.target.value as RuleDraft["minSeverity"])}
          >
            {SEVERITIES.map((s) => (
              <option key={String(s)} value={String(s)}>
                {SEVERITY_LABEL[String(s)]}
              </option>
            ))}
          </select>
          <FieldError message={err.min_severity} />
        </label>
      )}
      {use.statuses && (
        <fieldset>
          <legend>Run ends as</legend>
          {RUN_STATUSES.map((s) => (
            <label key={s} className="chip">
              <input
                type="checkbox"
                checked={d.runStatuses.includes(s)}
                onChange={() => set("runStatuses", toggle(d.runStatuses, s))}
              />
              {RUN_STATUS_WORD[s].replace(/^./, (c) => c.toUpperCase())}
            </label>
          ))}
          <FieldError message={err.run_statuses} />
        </fieldset>
      )}
      {use.threshold && (
        <label className="field">
          Daily spend limit (US dollars)
          <input
            type="number"
            min={0}
            step="any"
            inputMode="decimal"
            value={d.thresholdUsd}
            onChange={(e) => set("thresholdUsd", e.target.value)}
          />
          <FieldError message={err.threshold_usd} />
        </label>
      )}
      {use.lead && (
        <label className="field">
          Tell me this long before it expires (minutes)
          <input
            type="number"
            min={1}
            max={1440}
            step="any"
            value={d.leadMinutes}
            onChange={(e) => set("leadMinutes", e.target.value)}
          />
          <FieldError message={err.expiring_lead_seconds} />
        </label>
      )}
      <fieldset>
        <legend>Send to</legend>
        {destinations.map((x) => {
          const h = destinationHealth(x);
          return (
            <label key={x.id} className="chip">
              <input
                type="checkbox"
                checked={d.destinationIds.includes(x.id)}
                onChange={() => set("destinationIds", toggle(d.destinationIds, x.id))}
              />
              {x.name} ({TYPE_WORD[x.type]}
              {x.state !== "active" ? `, ${h.word.toLowerCase()}` : ""})
            </label>
          );
        })}
        <FieldError message={err.destination_ids} />
      </fieldset>
      <label className="field">
        Cooldown (minutes)
        <input
          type="number"
          min={0}
          max={10080}
          step="any"
          value={d.cooldownMinutes}
          onChange={(e) => set("cooldownMinutes", e.target.value)}
        />
        <span className="muted">
          After one alert, further ones to the same destination within this time are skipped. 0
          means no cooldown.
        </span>
        <FieldError message={err.cooldown_seconds} />
      </label>
      <label className="al-check">
        <input
          type="checkbox"
          checked={d.enabled}
          onChange={(e) => set("enabled", e.target.checked)}
        />
        Rule is on (turn it off to mute it)
      </label>
      <FieldError message={err.name} />
      <label className="field">
        Name
        <input
          value={d.name}
          maxLength={80}
          autoComplete="off"
          onChange={(e) => set("name", e.target.value)}
        />
      </label>
      {rule ? (
        <PreviewLine ruleId={rule.id} />
      ) : (
        <p className="muted">
          After you save, this page shows how often the rule would have fired in the last 7 days.
        </p>
      )}
      <AlertProblem error={mutation.error} kind="rule" />
      <p className="al-actions">
        <button type="submit" className="primary" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving…" : rule ? "Save rule" : "Create rule"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </p>
    </form>
  );
}
