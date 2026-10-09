"use client";

import { useState } from "react";
import { fieldErrors } from "@/lib/api/client";
import { useCan, useWorkspace } from "./WorkspaceProvider";
import { useCreateOverride, usePrices, useRebuildCosts } from "@/lib/admin";
import { ActionError, FieldError, when } from "./SettingsKit";
import { Empty, ErrorState, Loading } from "./States";

export function PricingOverrides() {
  const canWrite = useCan("pricing.write");
  const prices = usePrices(true);
  const rebuild = useRebuildCosts();
  return (
    <>
      <h1>Pricing</h1>
      <p className="muted">
        Prices per million tokens used to cost LLM calls. An override replaces the built-in price
        for matching models from its start time; past runs change only after a rebuild.
      </p>
      {canWrite && <OverrideForm />}
      {prices.isPending ? (
        <Loading label="Loading prices" />
      ) : prices.error ? (
        <ErrorState error={prices.error} onRetry={() => void prices.refetch()} />
      ) : prices.data.length === 0 ? (
        <Empty title="No prices" />
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Model prices</caption>
            <thead>
              <tr>
                <th>Model</th>
                <th>Origin</th>
                <th className="num">Input</th>
                <th className="num">Output</th>
                <th className="num">Cached input</th>
                <th>Valid from</th>
                <th>Scope</th>
              </tr>
            </thead>
            <tbody>
              {prices.data.map((p, i) => (
                <tr key={`${p.pricing_version}-${p.model_pattern}-${i}`}>
                  <td>{p.model_pattern}</td>
                  <td>{p.origin}</td>
                  <td className="num">{p.input_per_million}</td>
                  <td className="num">{p.output_per_million}</td>
                  <td className="num">{p.cached_input_per_million ?? "—"}</td>
                  <td>{when(p.valid_from)}</td>
                  <td>{p.project_id ?? "workspace"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {canWrite && (
        <>
          <h2>Apply to past runs</h2>
          <p className="muted">Recomputes the cost of existing runs with the prices above.</p>
          <button
            type="button"
            disabled={rebuild.isPending}
            onClick={() => rebuild.mutate({ limit: 10000 })}
          >
            {rebuild.isPending ? "Queuing…" : "Rebuild costs"}
          </button>
          <ActionError error={rebuild.error} />
          {rebuild.data && (
            <p role="status">
              Matched {rebuild.data.matched} runs, queued {rebuild.data.queued}
              {rebuild.data.truncated ? "; older runs were left out (run it again later)" : ""}.
            </p>
          )}
        </>
      )}
    </>
  );
}

function OverrideForm() {
  const ws = useWorkspace();
  const create = useCreateOverride();
  const [f, setF] = useState({
    model_pattern: "",
    input_per_million: "",
    output_per_million: "",
    cached_input_per_million: "",
    project_id: "",
    note: "",
    valid_from: "",
  });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF((cur) => ({ ...cur, [k]: e.target.value }));
  const errors = fieldErrors(create.error);
  return (
    <>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({
            model_pattern: f.model_pattern,
            input_per_million: f.input_per_million,
            output_per_million: f.output_per_million,
            cached_input_per_million: f.cached_input_per_million || null,
            project_id: f.project_id || null,
            note: f.note || null,
            request_price: "0",
            // The API's own default (always applies) unless a start time is given.
            valid_from: f.valid_from
              ? new Date(f.valid_from).toISOString()
              : "1970-01-01T00:00:00Z",
          });
        }}
      >
        <label className="field">
          Model pattern
          <input
            required
            value={f.model_pattern}
            onChange={set("model_pattern")}
            placeholder="my-model*"
          />
          <FieldError message={errors.model_pattern} />
        </label>
        <label className="field">
          Input per million
          <input
            required
            inputMode="decimal"
            value={f.input_per_million}
            onChange={set("input_per_million")}
          />
          <FieldError message={errors.input_per_million} />
        </label>
        <label className="field">
          Output per million
          <input
            required
            inputMode="decimal"
            value={f.output_per_million}
            onChange={set("output_per_million")}
          />
          <FieldError message={errors.output_per_million} />
        </label>
        <label className="field">
          Cached input per million
          <input
            inputMode="decimal"
            value={f.cached_input_per_million}
            onChange={set("cached_input_per_million")}
          />
          <FieldError message={errors.cached_input_per_million} />
        </label>
        <label className="field">
          Project
          <select value={f.project_id} onChange={set("project_id")}>
            <option value="">Whole workspace</option>
            {ws.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Valid from (optional)
          <input type="datetime-local" value={f.valid_from} onChange={set("valid_from")} />
          <FieldError message={errors.valid_from} />
        </label>
        <label className="field">
          Note
          <input value={f.note} maxLength={500} onChange={set("note")} />
          <FieldError message={errors.note} />
        </label>
        <button type="submit" className="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Add override"}
        </button>
      </form>
      {Object.keys(errors).length === 0 && <ActionError error={create.error} />}
      {create.isSuccess && <p role="status">Override added.</p>}
    </>
  );
}
