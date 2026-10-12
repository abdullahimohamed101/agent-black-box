"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { fieldErrors } from "@/lib/api/client";
import type { EvaluateOut, ExperimentOut, VariantMetricsOut } from "@/lib/api/types";
import { JUDGE_BADGE, KIND_LABEL } from "@/lib/evaluations";
import {
  EVALUATE_CAP,
  baselineRunId,
  configEntries,
  controlOf,
  distCost,
  distDuration,
  failureText,
  num,
  parseConfig,
  pct,
  warningText,
} from "@/lib/experiments";
import { formatTimestamp } from "@/lib/format";
import {
  useAddVariant,
  useAttachRun,
  useDetachRun,
  useEvaluateExperiment,
  useExperiment,
  useRuns,
} from "@/lib/queries";
import { comparePath, experimentsPath, runPath, type Base } from "@/lib/routes";
import { useCan, useProjectId } from "./WorkspaceProvider";
import { TableScroll } from "./TableScroll";
import { StatusBadge } from "./StatusBadge";
import { ErrorState, Loading } from "./States";
import { ActionError, FieldError } from "./SettingsKit";
import type { RunStatus } from "@/lib/api/types";

function ControlBanner({ e }: { e: ExperimentOut }) {
  const c = controlOf(e);
  if (c.state === "no-candidates")
    return (
      <p role="note" className="notice" data-testid="control-banner" data-control="no-candidates">
        Add a candidate variant to compare against the baseline.
      </p>
    );
  const names = (keys: string[]) => (keys.length ? keys.join(", ") : "nothing");
  return (
    <div role="note" className="notice" data-testid="control-banner" data-control={c.state}>
      {c.state === "single" ? (
        <p>
          <strong>Controlled:</strong> each candidate changes exactly one setting from the baseline,
          so a difference in results can be attributed to it.
        </p>
      ) : (
        <p>
          <strong>Not controlled:</strong> a candidate changes more than one setting (or none), so a
          difference in results cannot be attributed to a single cause.
        </p>
      )}
      <ul className="plain">
        {c.changes.map((x) => (
          <li key={x.variant}>
            {x.variant} changes: {names(x.keys)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Variants({ e }: { e: ExperimentOut }) {
  return (
    <TableScroll label="Variants">
      <table className="compare-table" data-testid="variants">
        <thead>
          <tr>
            <th scope="col">Variant</th>
            <th scope="col">Role</th>
            <th scope="col">Declared settings</th>
            <th scope="col">Differs from baseline</th>
            <th scope="col">Runs</th>
          </tr>
        </thead>
        <tbody>
          {e.variants.map((v) => (
            <tr key={v.id} data-role={v.role}>
              <th scope="row">{v.name}</th>
              <td>{v.role}</td>
              <td>
                <dl className="fields">
                  {configEntries(v.config).map(([k, val]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{val}</dd>
                    </div>
                  ))}
                </dl>
              </td>
              <td>
                {v.changed_variables === null ? "—" : v.changed_variables.join(", ") || "nothing"}
              </td>
              <td>{v.run_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

type MetricLine = { label: string; cells: (m: VariantMetricsOut) => string };

function Metrics({ e }: { e: ExperimentOut }) {
  const by = new Map(e.metrics.map((m) => [m.variant_id, m] as const));
  const lines: MetricLine[] = [
    { label: "Runs (finished)", cells: (m) => `${m.runs} (${m.finished})` },
    { label: "Success rate", cells: (m) => pct(m.success_rate) },
    { label: "Cost, mean", cells: (m) => distCost(m.cost_usd).mean },
    { label: "Cost, median", cells: (m) => distCost(m.cost_usd).median },
    { label: "Cost, 95th percentile", cells: (m) => distCost(m.cost_usd).p95 },
    { label: "Duration, mean", cells: (m) => distDuration(m.duration_ms).mean },
    { label: "Duration, median", cells: (m) => distDuration(m.duration_ms).median },
    { label: "Duration, 95th percentile", cells: (m) => distDuration(m.duration_ms).p95 },
    { label: "Retry rate", cells: (m) => pct(m.retry_rate) },
    { label: "Tool calls, mean", cells: (m) => num(m.tool_calls_mean, 1) },
    { label: "Unsuccessful runs", cells: failureText },
  ];
  const evalLines: { key: string; label: string; opinion: boolean; cells: (m: VariantMetricsOut) => string }[] =
    e.evaluators.flatMap((ev) => {
      const find = (m: VariantMetricsOut) => m.evaluations.find((x) => x.evaluator_id === ev.id);
      const name = `${ev.name}${ev.archived ? " (archived)" : ""}`;
      return [
        { key: `${ev.id}-s`, label: `${name}: mean score`, opinion: !ev.ground_truth, cells: (m: VariantMetricsOut) => num(find(m)?.mean_score, 3) },
        { key: `${ev.id}-p`, label: `${name}: pass rate`, opinion: !ev.ground_truth, cells: (m: VariantMetricsOut) => pct(find(m)?.pass_rate) },
        { key: `${ev.id}-c`, label: `${name}: completed / failed`, opinion: !ev.ground_truth, cells: (m: VariantMetricsOut) => { const x = find(m); return x ? `${x.completed} / ${x.failed}` : "—"; } },
      ];
    }); // prettier-ignore
  return (
    // A table wider than a phone scrolls inside its own region, which must be reachable by keyboard.
    <TableScroll label="Metrics by variant">
      <table className="compare-table" data-testid="metrics">
        <thead>
          <tr>
            <th scope="col">Measure</th>
            {e.variants.map((v) => (
              <th key={v.id} scope="col">
                {v.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.label}>
              <th scope="row">{l.label}</th>
              {e.variants.map((v) => {
                const m = by.get(v.id);
                return <td key={v.id}>{m ? l.cells(m) : "—"}</td>;
              })}
            </tr>
          ))}
          {evalLines.map((l) => (
            <tr key={l.key} data-group={l.opinion ? "judge" : "evaluator"}>
              <th scope="row">
                {l.label}
                {l.opinion && (
                  <>
                    {" "}
                    <span className="badge" data-tone="warn">
                      {JUDGE_BADGE}
                    </span>
                  </>
                )}
              </th>
              {e.variants.map((v) => {
                const m = by.get(v.id);
                return <td key={v.id}>{m ? l.cells(m) : "—"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function AttachRun({ e }: { e: ExperimentOut }) {
  const id = useId();
  const attach = useAttachRun(e.id);
  const projectId = useProjectId(e.project_id) ?? e.project_id;
  const runs = useRuns({ project: projectId }, 50);
  const [runId, setRunId] = useState("");
  const [variantId, setVariantId] = useState("");
  const attached = new Set(e.runs.map((r) => r.run_id));
  const options = (runs.data?.pages.flatMap((p) => p.items) ?? []).filter(
    (r) => !attached.has(r.id),
  );
  const chosenRun = options.some((r) => r.id === runId) ? runId : (options[0]?.id ?? "");
  const chosenVariant = e.variants.some((v) => v.id === variantId)
    ? variantId
    : (e.variants[0]?.id ?? "");
  const errors = fieldErrors(attach.error);
  return (
    <form
      className="experiment-form"
      aria-label="Attach a run"
      onSubmit={(ev) => {
        ev.preventDefault();
        if (chosenRun && chosenVariant)
          attach.mutate({ run_id: chosenRun, variant_id: chosenVariant });
      }}
    >
      <h3>Attach a run</h3>
      {runs.isPending ? (
        <Loading label="Loading runs" />
      ) : e.variants.length === 0 ? (
        <p className="muted">Add a variant first.</p>
      ) : options.length === 0 ? (
        <p className="muted">No unattached runs in this project.</p>
      ) : (
        <>
          <div className="field">
            <label htmlFor={`${id}-run`}>Run</label>
            <select id={`${id}-run`} value={chosenRun} onChange={(x) => setRunId(x.target.value)}>
              {options.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.id} ({r.status}, {formatTimestamp(r.started_at)})
                </option>
              ))}
            </select>
            <FieldError message={errors["run_id"]} />
          </div>
          <div className="field">
            <label htmlFor={`${id}-variant`}>Variant</label>
            <select
              id={`${id}-variant`}
              value={chosenVariant}
              onChange={(x) => setVariantId(x.target.value)}
            >
              {e.variants.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.role})
                </option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={attach.isPending}>
            {attach.isPending ? "Attaching…" : "Attach run"}
          </button>
        </>
      )}
      <ActionError error={attach.error} />
    </form>
  );
}

function AddVariant({ e }: { e: ExperimentOut }) {
  const id = useId();
  const add = useAddVariant(e.id);
  const hasBaseline = e.variants.some((v) => v.role === "baseline");
  const [name, setName] = useState("");
  const [role, setRole] = useState<"baseline" | "candidate">(
    hasBaseline ? "candidate" : "baseline",
  );
  const [text, setText] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const errors = fieldErrors(add.error);
  const effectiveRole = hasBaseline ? "candidate" : role;
  return (
    <form
      className="experiment-form"
      aria-label="Add a variant"
      onSubmit={(ev) => {
        ev.preventDefault();
        const parsed = parseConfig(text);
        setFormError(parsed.error);
        if (parsed.error || !name.trim()) return;
        add.mutate(
          { name: name.trim(), role: effectiveRole, config: parsed.config },
          {
            onSuccess: () => {
              setName("");
              setText("");
            },
          },
        );
      }}
    >
      <h3>Add a variant</h3>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input
          id={`${id}-name`}
          value={name}
          maxLength={64}
          onChange={(x) => setName(x.target.value)}
        />
        <FieldError message={errors["name"]} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-role`}>Role</label>
        <select
          id={`${id}-role`}
          value={effectiveRole}
          disabled={hasBaseline}
          onChange={(x) => setRole(x.target.value as "baseline" | "candidate")}
        >
          <option value="baseline">baseline</option>
          <option value="candidate">candidate</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${id}-config`}>Declared settings, one key=value per line</label>
        <textarea
          id={`${id}-config`}
          value={text}
          rows={3}
          placeholder={"model=gpt-x\nprompt_version=v2"}
          onChange={(x) => setText(x.target.value)}
        />
        <p className="muted">
          Visible to everyone who can see this experiment. No secrets, prompts or paths.
        </p>
        <FieldError message={formError ?? errors["config"]} />
      </div>
      <button type="submit" disabled={add.isPending}>
        {add.isPending ? "Adding…" : "Add variant"}
      </button>
      <ActionError error={add.error} />
    </form>
  );
}

function EvaluateAll({ e }: { e: ExperimentOut }) {
  const run = useEvaluateExperiment(e.id);
  const result: EvaluateOut | undefined = run.data;
  return (
    <div className="evaluate-all">
      <button
        type="button"
        disabled={run.isPending || e.runs.length === 0 || e.evaluators.length === 0}
        onClick={() => run.mutate()}
      >
        {run.isPending ? "Starting…" : "Evaluate all"}
      </button>
      <p className="muted">
        Starts every selected evaluator on every attached run (at most {EVALUATE_CAP} evaluations
        per click; click again while some remain). Evaluations start after a short batch delay so
        that runs still being summarised go first. A model judge costs money per evaluation.
      </p>
      <ActionError error={run.error} />
      {result && (
        <div role="status" data-testid="evaluate-result">
          <p>
            Started {result.enqueued} evaluation(s) on {result.jobs} run(s).
            {result.remaining > 0 && <> {result.remaining} remain: click again to start them.</>}
            {result.at_limit > 0 && (
              <> {result.at_limit} skipped: the evaluator already has 20 results for the run.</>
            )}
          </p>
          {result.skipped_evaluators.length > 0 && (
            <ul className="plain">
              {result.skipped_evaluators.map((s) => (
                <li key={s.evaluator_id}>
                  Evaluator {s.evaluator_id} skipped: {s.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Attached({ e, base, canWrite }: { e: ExperimentOut; base: Base; canWrite: boolean }) {
  const detach = useDetachRun(e.id);
  const baseline = baselineRunId(e);
  const name = (id: string) => e.variants.find((v) => v.id === id);
  if (e.runs.length === 0) return <p className="muted">No runs are attached yet.</p>;
  return (
    <>
      <TableScroll label="Attached runs">
        <table className="compare-table" data-testid="attached-runs">
          <thead>
            <tr>
              <th scope="col">Run</th>
              <th scope="col">Variant</th>
              <th scope="col">Status</th>
              <th scope="col">Started</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {e.runs.map((r) => {
              const v = name(r.variant_id);
              return (
                <tr key={r.run_id}>
                  <th scope="row">
                    <Link href={runPath(base, r.run_id)}>{r.run_id}</Link>
                  </th>
                  <td>{v?.name ?? "—"}</td>
                  <td>
                    <StatusBadge status={r.status as RunStatus} />
                  </td>
                  <td>{formatTimestamp(r.started_at)}</td>
                  <td>
                    {v?.role === "candidate" && baseline && (
                      <Link href={comparePath(base, baseline, r.run_id)}>
                        Compare with baseline run
                      </Link>
                    )}{" "}
                    {canWrite && (
                      <button
                        type="button"
                        disabled={detach.isPending}
                        onClick={() => detach.mutate(r.run_id)}
                      >
                        Detach
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <ActionError error={detach.error} />
    </>
  );
}

/** One experiment: variants and their declared settings, whether the comparison is controlled, and the metrics. */
export function ExperimentDetail({ base, experimentId }: { base: Base; experimentId: string }) {
  const q = useExperiment(experimentId);
  const canWrite = useCan("experiment.write");
  const canRun = useCan("evaluation.run");
  const ids = [useId(), useId(), useId(), useId()] as const;
  if (q.isPending) return <Loading label="Loading experiment" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const e = q.data;
  return (
    <div className="experiment" data-testid="experiment">
      <p>
        <Link href={experimentsPath(base)}>All experiments</Link>
      </p>
      <h1>{e.name}</h1>
      {e.description && <p>{e.description}</p>}
      {e.dataset_label && <p className="muted">Task set: {e.dataset_label}</p>}
      {e.archived_at && <p className="notice">This experiment is archived.</p>}
      <ControlBanner e={e} />
      {e.warnings.length > 0 && (
        <ul className="plain" aria-label="Warnings" data-testid="warnings">
          {e.warnings.map((w, i) => (
            <li key={i} data-code={w.code}>
              <span className="badge" data-tone="warn">
                Warning
              </span>{" "}
              {warningText(w)}
              {w.keys && w.keys.length > 0 && <> ({w.keys.join(", ")})</>}
              {w.count != null && <> ({w.count})</>}
            </li>
          ))}
        </ul>
      )}
      <section aria-labelledby={ids[0]}>
        <h2 id={ids[0]}>Variants</h2>
        <Variants e={e} />
        {canWrite && <AddVariant e={e} />}
      </section>
      <section aria-labelledby={ids[1]}>
        <h2 id={ids[1]}>Metrics</h2>
        {e.evaluators.length > 0 && (
          <p className="muted">
            Evaluators:{" "}
            {e.evaluators.map((v) => `${v.name} (${KIND_LABEL[v.kind].toLowerCase()})`).join(", ")}.
          </p>
        )}
        <Metrics e={e} />
        {canRun && <EvaluateAll e={e} />}
      </section>
      <section aria-labelledby={ids[2]}>
        <h2 id={ids[2]}>Runs</h2>
        <Attached e={e} base={base} canWrite={canWrite} />
        {canWrite && <AttachRun e={e} />}
      </section>
    </div>
  );
}
