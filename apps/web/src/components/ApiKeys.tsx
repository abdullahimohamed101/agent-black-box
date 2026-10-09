"use client";

import { useState } from "react";
import { fieldErrors } from "@/lib/api/client";
import { KEY_SCOPES, type KeyScope } from "@/lib/api/types";
import { useApiKeys, useCreateKey, useRevokeKey } from "@/lib/admin";
import { ActionError, FieldError, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";
import { useCan, useWorkspace } from "./WorkspaceProvider";

export function ApiKeys() {
  const ws = useWorkspace();
  const canCreate = useCan("api_key.create");
  const canRevokeAny = useCan("api_key.revoke");
  const keys = useApiKeys(true);
  const revoke = useRevokeKey();
  return (
    <>
      <h1>API keys</h1>
      <p className="muted">
        Keys let SDKs and tools send events and read runs. A token is shown once, when the key is
        created.
      </p>
      {canCreate && <CreateKeyForm />}
      {keys.isPending ? (
        <Loading label="Loading API keys" />
      ) : keys.error ? (
        <ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
      ) : keys.data.length === 0 ? (
        <Empty title="No API keys">Create one to connect an agent.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">API keys</caption>
            <thead>
              <tr>
                <th>Name</th>
                <th>Key id</th>
                <th>Scopes</th>
                <th>Project</th>
                <th>Last used</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.data.map((k) => {
                const mine = !!ws.user && k.created_by === ws.user.id;
                // Own-only grants (`own_permissions`) cover keys the person created themselves.
                const mayRevoke =
                  canRevokeAny || (mine && ws.ownPermissions.includes("api_key.revoke"));
                const project = ws.projects.find((p) => p.id === k.project_id);
                return (
                  <tr key={k.key_id}>
                    <td>{k.name ?? <span className="muted">unnamed</span>}</td>
                    <td className="meta">{k.key_id}</td>
                    <td>{k.scopes.join(", ")}</td>
                    <td>{k.project_id ? (project?.name ?? k.project_id) : "workspace"}</td>
                    <td>{when(k.last_used_at)}</td>
                    <td>{k.status}</td>
                    <td>
                      {mayRevoke ? (
                        <button
                          type="button"
                          className="danger"
                          aria-label={`Revoke key ${k.name ?? k.key_id}`}
                          onClick={() => revoke.mutate(k.key_id)}
                        >
                          Revoke
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <ActionError error={revoke.error} />
    </>
  );
}

function CreateKeyForm() {
  const ws = useWorkspace();
  const create = useCreateKey();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<KeyScope[]>(["events:write"]);
  const [project, setProject] = useState("");
  const [days, setDays] = useState("");
  const errors = fieldErrors(create.error);
  const toggle = (s: KeyScope) =>
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  return (
    <>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({
            name: name || null,
            scopes,
            project_id: project || null,
            expires_in_days: days ? Number(days) : null,
          });
        }}
      >
        <label className="field">
          Name
          <input value={name} maxLength={128} onChange={(e) => setName(e.target.value)} />
          <FieldError message={errors.name} />
        </label>
        <fieldset>
          <legend>Scopes</legend>
          {KEY_SCOPES.map((s) => (
            <label key={s} className="chip">
              <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
              {s}
            </label>
          ))}
          <FieldError message={errors.scopes} />
        </fieldset>
        <label className="field">
          Project
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">Whole workspace</option>
            {ws.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <FieldError message={errors.project_id} />
        </label>
        <label className="field">
          Expires in (days)
          <input
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <FieldError message={errors.expires_in_days} />
        </label>
        <button
          type="submit"
          className="primary"
          disabled={create.isPending || scopes.length === 0}
        >
          {create.isPending ? "Creating…" : "Create key"}
        </button>
      </form>
      {Object.keys(errors).length === 0 && <ActionError error={create.error} />}
      {create.data && (
        <div role="status" className="withheld">
          <p>
            Key <strong>{create.data.key.name ?? create.data.key.key_id}</strong> created. Copy the
            token now: it is shown once and the server keeps only a hash.
          </p>
          <p className="secret" data-testid="key-token">
            {create.data.token}
          </p>
          <button type="button" onClick={() => create.reset()}>
            I have stored it
          </button>
        </div>
      )}
    </>
  );
}
