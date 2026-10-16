"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { experiments as t } from "@/copy/experiments";
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
  cheapestOf,
  verdictOf,
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
import { BarList } from "./charts";
import { TitleLine } from "./TitleLine";
import { useCan, useProjectId } from "./WorkspaceProvider";
import { TableScroll } from "./TableScroll";
import { StatusBadge } from "./StatusBadge";
import { ErrorState, Loading } from "./States";
import { ActionError, FieldError } from "./SettingsKit";
import type { RunStatus } from "@/lib/api/types";
import "../app/experiments.css";

function ControlBanner({ e }: { e: ExperimentOut }) {
  const c = controlOf(e);
  if (c.state === "no-candidates")
    return (
      <p role="note" className="notice" data-testid="control-banner" data-control="no-candidates">
        {t.control.none}
      </p>
    );
  const names = (keys: string[]) => (keys.length ? keys.join(", ") : t.control.nothing);
  return (
    <div role="note" className="notice" data-testid="control-banner" data-control={c.state}>
      {c.state === "single" ? (
        <p>
          <strong>{t.control.single}</strong> {t.control.singleBody}
        </p>
      ) : (
        <p>
          <strong>{t.control.multiple}</strong> {t.control.multipleBody}
        </p>
      )}
      <ul className="plain">
        {c.changes.map((x) => (
          <li key={x.variant}>{t.control.changes(x.variant, names(x.keys))}</li>
        ))}
      </ul>
    </div>
  );
}

function Variants({ e }: { e: ExperimentOut }) {
  return (
    <TableScroll label={t.variants.caption}>
      <table data-testid="variants" className="ex-stack">
        <thead>
          <tr>
            <th scope="col">{t.variants.heads.variant}</th>
            <th scope="col">{t.variants.heads.role}</th>
            <th scope="col">{t.variants.heads.settings}</th>
            <th scope="col">{t.variants.heads.differs}</th>
            <th scope="col" className="num">
              {t.variants.heads.runs}
            </th>
          </tr>
        </thead>
        <tbody>
          {e.variants.map((v) => (
            <tr key={v.id} data-role={v.role}>
              <th scope="row">{v.name}</th>
              <td data-label={t.variants.heads.role}>{v.role}</td>
              <td data-label={t.variants.heads.settings}>
                <dl className="fields">
                  {configEntries(v.config).map(([k, val]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{val}</dd>
                    </div>
                  ))}
                </dl>
              </td>
              <td data-label={t.variants.heads.differs}>
                {v.changed_variables === null
                  ? "—"
                  : v.changed_variables.join(", ") || t.variants.nothing}
              </td>
              <td className="num" data-label={t.variants.heads.runs}>
                {v.run_count}
              </td>
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
    <TableScroll label={t.metrics.caption}>
      <table data-testid="metrics">
        <thead>
          <tr>
            <th scope="col">{t.metrics.measure}</th>
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
      aria-label={t.attach.title}
      onSubmit={(ev) => {
        ev.preventDefault();
        if (chosenRun && chosenVariant)
          attach.mutate({ run_id: chosenRun, variant_id: chosenVariant });
      }}
    >
      <h3>{t.attach.title}</h3>
      {runs.isPending ? (
        <Loading label={t.attach.loading} />
      ) : e.variants.length === 0 ? (
        <p className="muted">{t.attach.needVariant}</p>
      ) : options.length === 0 ? (
        <p className="muted">{t.attach.none}</p>
      ) : (
        <>
          <div className="field">
            <label htmlFor={`${id}-run`}>{t.attach.run}</label>
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
            <label htmlFor={`${id}-variant`}>{t.attach.variant}</label>
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
          <button type="submit" className="primary" disabled={attach.isPending}>
            {attach.isPending ? t.attach.submitting : t.attach.submit}
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
      aria-label={t.addVariant.title}
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
      <h3>{t.addVariant.title}</h3>
      <div className="field">
        <label htmlFor={`${id}-name`}>{t.addVariant.name}</label>
        <input
          id={`${id}-name`}
          value={name}
          maxLength={64}
          onChange={(x) => setName(x.target.value)}
        />
        <FieldError message={errors["name"]} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-role`}>{t.addVariant.role}</label>
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
        <label htmlFor={`${id}-config`}>{t.addVariant.config}</label>
        <textarea
          id={`${id}-config`}
          value={text}
          rows={3}
          placeholder={"model=gpt-x\nprompt_version=v2"}
          onChange={(x) => setText(x.target.value)}
        />
        <p className="muted">{t.addVariant.help}</p>
        <FieldError message={formError ?? errors["config"]} />
      </div>
      <button type="submit" className="primary" disabled={add.isPending}>
        {add.isPending ? t.addVariant.submitting : t.addVariant.submit}
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
        {run.isPending ? t.evaluate.starting : t.evaluate.button}
      </button>
      <p className="muted">{t.evaluate.help(EVALUATE_CAP)}</p>
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
  if (e.runs.length === 0) return <p className="muted">{t.attached.none}</p>;
  return (
    <>
      <TableScroll label={t.attached.caption}>
        <table data-testid="attached-runs" className="ex-stack">
          <thead>
            <tr>
              <th scope="col">{t.attached.heads.run}</th>
              <th scope="col">{t.attached.heads.variant}</th>
              <th scope="col">{t.attached.heads.status}</th>
              <th scope="col">{t.attached.heads.started}</th>
              <th scope="col">{t.attached.heads.actions}</th>
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
                  <td data-label={t.attached.heads.variant}>{v?.name ?? "—"}</td>
                  <td data-label={t.attached.heads.status}>
                    <StatusBadge status={r.status as RunStatus} />
                  </td>
                  <td data-label={t.attached.heads.started}>{formatTimestamp(r.started_at)}</td>
                  <td data-label={t.attached.heads.actions}>
                    {v?.role === "candidate" && baseline && (
                      <Link href={comparePath(base, baseline, r.run_id)}>{t.attached.compare}</Link>
                    )}{" "}
                    {canWrite && (
                      <button
                        type="button"
                        disabled={detach.isPending}
                        onClick={() => detach.mutate(r.run_id)}
                      >
                        {t.attached.detach}
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
  if (q.isPending) return <Loading label={t.loadingOne} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const e = q.data;
  const verdict = verdictOf(e);
  const byVariant = new Map(e.metrics.map((m) => [m.variant_id, m] as const));
  const rated = e.variants.flatMap((v) => {
    const m = byVariant.get(v.id);
    return m && m.finished > 0 ? [{ v, m }] : [];
  });
  // The cost chart marks the cheaper variant: the verdict's leader may be the one that cost more.
  const cheapest = cheapestOf(rated.map(({ v, m }) => ({ id: v.id, median: m.cost_usd.median })));
  return (
    <div className="experiment st" data-testid="experiment">
      <TitleLine href={experimentsPath(base)} title={e.name} back={t.back} />
      {e.description && <p className="ex-lede">{e.description}</p>}
      {e.dataset_label && <p className="muted ex-meta">{t.detail.taskSet(e.dataset_label)}</p>}
      {e.archived_at && <p className="notice">{t.detail.archived}</p>}
      <p className="ex-verdict" data-testid="experiment-verdict" data-verdict={verdict.kind}>
        {verdict.text}
      </p>
      <ControlBanner e={e} />
      {e.warnings.length > 0 && (
        <ul className="plain" aria-label="Warnings" data-testid="warnings">
          {e.warnings.map((w, i) => (
            <li key={i} data-code={w.code}>
              <span className="badge" data-tone="warn">
                {t.detail.warning}
              </span>{" "}
              {warningText(w)}
              {w.keys && w.keys.length > 0 && <> ({w.keys.join(", ")})</>}
              {w.count != null && <> ({w.count})</>}
            </li>
          ))}
        </ul>
      )}
      {rated.length > 0 && (
        <section className="ex-block ex-charts" aria-labelledby={ids[3]}>
          <h2 id={ids[3]}>{t.sections.compared}</h2>
          <div className="two-col">
            <div>
              <h3>{t.sections.successRate}</h3>
              <BarList
                caption={t.chart.successRate}
                marks="lead"
                bars={rated.map(({ v, m }) => ({
                  label: v.name,
                  value: m.success_rate ?? 0,
                  display: pct(m.success_rate),
                  lead: v.id === verdict.leader,
                }))}
              />
            </div>
            <div>
              <h3>{t.sections.medianCost}</h3>
              <BarList
                caption={t.chart.medianCost}
                marks="lead"
                bars={rated.map(({ v, m }) => ({
                  label: v.name,
                  value: m.cost_usd.median ?? 0,
                  display: distCost(m.cost_usd).median,
                  lead: v.id === cheapest,
                }))}
              />
            </div>
          </div>
        </section>
      )}
      <section className="ex-block" aria-labelledby={ids[0]}>
        <h2 id={ids[0]}>{t.sections.variants}</h2>
        <Variants e={e} />
        {canWrite && <AddVariant e={e} />}
      </section>
      <section className="ex-block" aria-labelledby={ids[1]}>
        <h2 id={ids[1]}>{t.sections.metrics}</h2>
        {e.evaluators.length > 0 && (
          <p className="muted">
            {t.metrics.evaluators(
              e.evaluators.map((v) => `${v.name} (${KIND_LABEL[v.kind].toLowerCase()})`).join(", "),
            )}
          </p>
        )}
        <Metrics e={e} />
        {canRun && <EvaluateAll e={e} />}
      </section>
      <section className="ex-block" aria-labelledby={ids[2]}>
        <h2 id={ids[2]}>{t.sections.runs}</h2>
        <Attached e={e} base={base} canWrite={canWrite} />
        {canWrite && <AttachRun e={e} />}
      </section>
    </div>
  );
}
