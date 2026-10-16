"use client";

import { useState } from "react";
import {
  TYPE_WORD,
  destinationHealth,
  errorCodeWords,
  isDenied,
  parseRecipients,
  problemFrom,
  type AlertDestination,
  type DestinationType,
} from "@/lib/alerts";
import {
  useAlertDestinations,
  useCreateDestination,
  useDeleteDestination,
  useTestDestination,
  useUpdateDestination,
  type SecretSink,
} from "@/lib/alertsApi";
import { AlertProblem, StatusMark } from "./AlertMarks";
import { SecretOncePanel } from "./SecretOncePanel";
import { FieldError } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan } from "./WorkspaceProvider";
import type { Intent } from "./AlertRules";
import "../app/alerts.css";

type Secret = { destinationName: string; secret: string };
type TestState =
  { state: "sending" } | { state: "done"; ok: boolean; code: string | null } | { state: "error" };

export function AlertDestinations({
  go,
  intent,
}: {
  go: (tab: "rules", intent: Intent) => void;
  intent: Intent;
}) {
  const canRead = useCan("alert.destination.read");
  const canWrite = useCan("alert.write");
  const q = useAlertDestinations(canRead);
  const update = useUpdateDestination();
  const remove = useDeleteDestination();
  const test = useTestDestination();
  const [adding, setAdding] = useState(intent?.kind === "destination");
  const [secret, setSecret] = useState<Secret | null>(null);
  const [confirm, setConfirm] = useState<{ id: string; what: "delete" | "rotate" } | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const sink: SecretSink = (s) => setSecret(s);

  // Other roles never ask: the explanation is the whole tab.
  if (!canRead || (q.isError && isDenied(q.error)))
    return (
      <div
        className="state"
        data-state="forbidden"
        role="note"
        data-testid="destinations-forbidden"
      >
        <p className="state-title">Destinations are for owners and admins</p>
        <p className="muted">
          A destination holds a private address or a signing secret, so only owners and admins can
          see or change them. You can still read the rules and the history of what was sent.
        </p>
      </div>
    );
  if (q.isPending) return <Loading label="Loading destinations" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;

  const items = q.data.items;
  const runTest = (id: string) => {
    setTests((t) => ({ ...t, [id]: { state: "sending" } }));
    test.mutate(id, {
      onSuccess: (r) =>
        setTests((t) => ({ ...t, [id]: { state: "done", ok: r.ok, code: r.error_code } })),
      onError: () => setTests((t) => ({ ...t, [id]: { state: "error" } })),
    });
  };

  return (
    <section aria-label="Alert destinations">
      {secret && (
        <SecretOncePanel
          destinationName={secret.destinationName}
          secret={secret.secret}
          onDismiss={() => setSecret(null)}
        />
      )}
      {canWrite && !adding && items.length > 0 && (
        <p className="al-add">
          <button type="button" className="primary" onClick={() => setAdding(true)}>
            Add a destination
          </button>
        </p>
      )}
      {canWrite && (adding || items.length === 0) && (
        <DestinationForm
          emailConfigured={q.data.email_configured}
          first={items.length === 0}
          onSecret={sink}
          onDone={() => setAdding(false)}
          onCancel={items.length > 0 ? () => setAdding(false) : undefined}
        />
      )}
      {items.length === 0 ? (
        <div className="al-empty" data-testid="alerts-empty">
          <Empty motif="none" title="Add a destination, then a rule">
            Alerts need somewhere to go. Add one above; then make a rule that uses it.
          </Empty>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="al-table">
            <caption className="sr-only">Alert destinations</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Target</th>
                <th scope="col">Health</th>
                <th scope="col">Rules</th>
                {canWrite && <th scope="col">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <DestinationRow
                  key={d.id}
                  d={d}
                  canWrite={canWrite}
                  test={tests[d.id]}
                  confirm={confirm?.id === d.id ? confirm.what : null}
                  busy={update.isPending || remove.isPending}
                  onTest={() => runTest(d.id)}
                  onPause={() =>
                    update.mutate({
                      id: d.id,
                      body: {
                        state: d.state === "paused" ? "active" : "paused",
                        rotate_signing_secret: false,
                      },
                    })
                  }
                  onAsk={(what) => setConfirm(what ? { id: d.id, what } : null)}
                  onRotate={() =>
                    update.mutate(
                      { id: d.id, body: { rotate_signing_secret: true }, onSecret: sink },
                      { onSettled: () => setConfirm(null) },
                    )
                  }
                  onDelete={() => remove.mutate(d.id, { onSettled: () => setConfirm(null) })}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AlertProblem error={update.error ?? remove.error} kind="destination" />
      {items.length > 0 && items.every((d) => d.rule_count === 0) && (
        <p className="muted">
          No rule uses a destination yet.{" "}
          <button type="button" onClick={() => go("rules", { kind: "starter" })}>
            Add a rule
          </button>
        </p>
      )}
    </section>
  );
}

function DestinationRow({
  d,
  canWrite,
  test,
  confirm,
  busy,
  onTest,
  onPause,
  onAsk,
  onRotate,
  onDelete,
}: {
  d: AlertDestination;
  canWrite: boolean;
  test: TestState | undefined;
  confirm: "delete" | "rotate" | null;
  busy: boolean;
  onTest: () => void;
  onPause: () => void;
  onAsk: (what: "delete" | "rotate" | null) => void;
  onRotate: () => void;
  onDelete: () => void;
}) {
  const h = destinationHealth(d);
  return (
    <tr data-testid="destination-row" data-state={d.state}>
      <td className="al-name">
        {d.name} <span className="muted">({TYPE_WORD[d.type]})</span>
      </td>
      <td className="al-target">{d.masked_target}</td>
      <td>
        <StatusMark meta={h} data-testid="health" />
        {h.detail && <p className="muted al-detail">{h.detail}</p>}
      </td>
      <td>{d.rule_count}</td>
      {canWrite && (
        <td className="al-row-actions">
          {confirm ? (
            <>
              <span>
                {confirm === "delete"
                  ? "Delete this destination? Rules left with no destination are turned off."
                  : "Rotate the secret? The old one stops working at once."}
              </span>{" "}
              <button
                type="button"
                className="danger"
                onClick={confirm === "delete" ? onDelete : onRotate}
              >
                {confirm === "delete" ? `Delete ${d.name}` : `Rotate ${d.name}`}
              </button>{" "}
              <button type="button" onClick={() => onAsk(null)}>
                Keep
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label={`Send test to ${d.name}`}
                disabled={test?.state === "sending"}
                onClick={onTest}
              >
                Send test
              </button>{" "}
              <button
                type="button"
                aria-label={`${d.state === "paused" ? "Resume" : "Pause"} ${d.name}`}
                disabled={busy}
                onClick={onPause}
              >
                {d.state === "paused" ? "Resume" : "Pause"}
              </button>{" "}
              {d.type === "webhook" && (
                <>
                  <button
                    type="button"
                    aria-label={`Rotate secret of ${d.name}`}
                    onClick={() => onAsk("rotate")}
                  >
                    Rotate secret
                  </button>{" "}
                </>
              )}
              <button
                type="button"
                className="danger"
                aria-label={`Delete ${d.name}`}
                onClick={() => onAsk("delete")}
              >
                Delete
              </button>
            </>
          )}
          <p role="status" className="al-detail" data-testid="test-result">
            {test?.state === "sending" && "Sending a test…"}
            {test?.state === "done" &&
              (test.ok
                ? "Test sent. The destination accepted it."
                : `Test failed. ${errorCodeWords(test.code)}`)}
            {test?.state === "error" && "The test could not be run. Try again."}
          </p>
        </td>
      )}
    </tr>
  );
}

function DestinationForm({
  emailConfigured,
  first,
  onSecret,
  onDone,
  onCancel,
}: {
  emailConfigured: boolean;
  first: boolean;
  onSecret: SecretSink;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const create = useCreateDestination();
  const [type, setType] = useState<DestinationType>("webhook");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [recipients, setRecipients] = useState("");
  const [local, setLocal] = useState<Record<string, string>>({});
  const server = problemFrom(create.error, "destination").fields;
  const err = { ...server, ...local };
  const email = type === "email";
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = "Give the destination a name.";
    const list = parseRecipients(recipients);
    if (email ? list.length === 0 : !url.trim())
      errors.target = email ? "Enter at least one email address." : "Enter the address.";
    setLocal(errors);
    if (Object.keys(errors).length) return;
    create.mutate(
      {
        body: {
          name: name.trim(),
          type,
          target: email ? { recipients: list } : { url: url.trim() },
        },
        onSecret,
      },
      {
        onSuccess: () => {
          // The address is a secret too: it does not stay in the form.
          setUrl("");
          setRecipients("");
          setName("");
          onDone();
        },
      },
    );
  };
  const urlLabel = type === "slack" ? "Slack incoming webhook URL" : "Webhook URL";
  return (
    <form className="al-form" onSubmit={submit} aria-label="New destination">
      <h2>{first ? "Add your first destination" : "New destination"}</h2>
      <label className="field">
        Type
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value as DestinationType);
            setLocal({});
            create.reset();
          }}
        >
          <option value="webhook">Webhook (signed JSON)</option>
          <option value="slack">Slack (incoming webhook)</option>
          <option value="email" disabled={!emailConfigured}>
            Email{emailConfigured ? "" : " (not set up on this server)"}
          </option>
        </select>
        {!emailConfigured && (
          <span className="muted" data-testid="email-unavailable">
            Email needs the person who runs Agent Black Box to set up outgoing mail first.
          </span>
        )}
      </label>
      <label className="field">
        Name
        <input
          value={name}
          maxLength={80}
          autoComplete="off"
          onChange={(e) => {
            setName(e.target.value);
            setLocal({});
          }}
        />
        <FieldError message={err.name} />
      </label>
      {email ? (
        <label className="field">
          Email addresses (up to 10, separated by commas or lines)
          <textarea
            rows={3}
            value={recipients}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => setRecipients(e.target.value)}
          />
          <FieldError message={err.target} />
        </label>
      ) : (
        <label className="field">
          {urlLabel}
          <input
            value={url}
            autoComplete="off"
            spellCheck={false}
            inputMode="url"
            placeholder="https://"
            onChange={(e) => setUrl(e.target.value)}
          />
          <span className="muted">
            Must be https and reachable from the internet. It is stored encrypted and never shown
            again.
          </span>
          <FieldError message={err.target} />
        </label>
      )}
      <AlertProblem error={create.error} kind="destination" />
      <p className="al-actions">
        <button type="submit" className="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Add destination"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </p>
    </form>
  );
}
