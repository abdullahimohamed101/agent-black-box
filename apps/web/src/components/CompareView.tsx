"use client";

import Link from "next/link";
import { useId } from "react";
import { useSearchParams } from "next/navigation";
import type { ComparisonOut, EvaluatorKind, RunStatus } from "@/lib/api/types";
import { deltaText, directionGlyph, metricRows, scoreText } from "@/lib/comparison";
import { KIND_LABEL, JUDGE_BADGE } from "@/lib/evaluations";
import { formatCost, formatInt } from "@/lib/format";
import { runPath, type Base } from "@/lib/routes";
import { useComparison } from "@/lib/queries";
import { AlignedSteps } from "./AlignedSteps";
import { TableScroll } from "./TableScroll";
import { StatusBadge } from "./StatusBadge";
import { Empty, ErrorState, Loading } from "./States";

function Notices({ c }: { c: ComparisonOut }) {
  return (
    <div className="compare-notices">
      {c.alignment === "metadata" && (
        <p role="note" className="notice" data-testid="metadata-notice">
          Divergence is computed on metadata only: step kinds, tool names and model names. Commands,
          file paths and other content are not compared for your role, so the divergence can differ
          from the one a person who can read content sees.
        </p>
      )}
      {c.alignment_quality === "partial" && (
        <p role="note" className="notice" data-testid="partial-notice">
          Partial alignment: the runs differ too much to align every step within the time budget.
          The first divergence is exact; the steps after it are shown as one block per side.
        </p>
      )}
      {([c.left, c.right] as const).map((s, i) =>
        s.truncated ? (
          <p key={i} role="note" className="notice" data-testid="truncated-notice">
            The {i === 0 ? "left" : "right"} run is too long to compare in full: only its first{" "}
            {formatInt(s.steps)} steps are compared.
          </p>
        ) : null,
      )}
    </div>
  );
}

function Summary({ c }: { c: ComparisonOut }) {
  const rows = metricRows(c.summary);
  return (
    <TableScroll label="Summary">
      <table className="compare-table" data-testid="compare-summary">
        <caption className="muted">Right minus left</caption>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            <th scope="col">Left</th>
            <th scope="col">Right</th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Status</th>
            <td>
              <StatusBadge status={c.left.status as RunStatus} />
            </td>
            <td>
              <StatusBadge status={c.right.status as RunStatus} />
            </td>
            <td>{c.left.status === c.right.status ? "same" : "differs"}</td>
          </tr>
          {rows.map((r) => (
            <tr key={r.metric} data-metric={r.metric}>
              <th scope="row">{r.label}</th>
              <td>{r.left}</td>
              <td>{r.right}</td>
              <td className="delta" data-direction={r.direction} data-tone={r.tone}>
                <span aria-hidden="true">{directionGlyph(r.direction)}</span> {r.delta}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function Evaluations({ c }: { c: ComparisonOut }) {
  if (c.evaluations.length === 0)
    return <p className="muted">Neither run has a completed evaluation.</p>;
  return (
    <TableScroll label="Evaluation scores">
      <table className="compare-table" data-testid="compare-evaluations">
        <thead>
          <tr>
            <th scope="col">Evaluator</th>
            <th scope="col">Left</th>
            <th scope="col">Right</th>
            <th scope="col">Score change</th>
          </tr>
        </thead>
        <tbody>
          {c.evaluations.map((e) => (
            <tr key={e.evaluator_id ?? e.evaluator_name}>
              <th scope="row">
                {e.evaluator_name}{" "}
                <span className="tag">{KIND_LABEL[e.kind as EvaluatorKind]}</span>
                {!e.ground_truth && (
                  <>
                    {" "}
                    <span className="badge" data-tone="warn">
                      {JUDGE_BADGE}
                    </span>
                  </>
                )}
              </th>
              <td>{scoreText(e.left)}</td>
              <td>{scoreText(e.right)}</td>
              <td>{deltaText(e.delta)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function CostByModel({ c }: { c: ComparisonOut }) {
  if (c.cost_by_model.length === 0) return <p className="muted">No priced model calls.</p>;
  return (
    <TableScroll label="Cost by model">
      <table className="compare-table" data-testid="compare-cost">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Left</th>
            <th scope="col">Right</th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          {c.cost_by_model.map((m) => (
            <tr key={m.model}>
              <th scope="row">{m.model}</th>
              <td>{formatCost(m.left)}</td>
              <td>{formatCost(m.right)}</td>
              <td>
                {m.delta === 0
                  ? "no change"
                  : `${m.delta > 0 ? "+" : "−"}${formatCost(Math.abs(m.delta))}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function PathList({ title, paths }: { title: string; paths: readonly string[] }) {
  return (
    <div>
      <h3>{title}</h3>
      {paths.length === 0 ? (
        <p className="muted">None.</p>
      ) : (
        <ul className="plain">
          {paths.map((p) => (
            <li key={p}>
              <code>{p}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Files({ c }: { c: ComparisonOut }) {
  const f = c.files;
  if (f.paths_withheld)
    return (
      <div data-testid="compare-files" data-withheld="true">
        <p>
          Left run modified {formatInt(f.left_count)} file(s); right run modified{" "}
          {formatInt(f.right_count)} file(s).
        </p>
        <p className="muted">
          File paths and the overlap between the runs are withheld for your role.
        </p>
      </div>
    );
  return (
    <div className="file-sets" data-testid="compare-files">
      <PathList title="Only in the left run" paths={f.only_left} />
      <PathList title="Only in the right run" paths={f.only_right} />
      <PathList title="In both" paths={f.both} />
      {f.truncated && <p className="muted">The lists are cut short.</p>}
    </div>
  );
}

function RunLink({
  base,
  id,
  status,
  side,
}: {
  base: Base;
  id: string;
  status: string;
  side: string;
}) {
  return (
    <p data-testid={`${side}-run`}>
      <span className="muted">{side === "left" ? "Left" : "Right"} run </span>
      <Link href={runPath(base, id)}>{id}</Link> <StatusBadge status={status as RunStatus} />
    </p>
  );
}

function Loaded({ c, base }: { c: ComparisonOut; base: Base }) {
  const ids = [useId(), useId(), useId(), useId(), useId()] as const;
  return (
    <>
      <div className="compare-runs">
        <RunLink base={base} id={c.left.run_id} status={c.left.status} side="left" />
        <RunLink base={base} id={c.right.run_id} status={c.right.status} side="right" />
      </div>
      <Notices c={c} />
      {c.divergence === null ? (
        <p data-testid="no-divergence">
          The runs took the same steps{c.alignment === "metadata" ? " (compared on metadata)" : ""}.
        </p>
      ) : (
        <p data-testid="divergence-summary">
          The runs first diverge at step {c.divergence.index + 1}
          {c.first_outcome_difference !== null &&
            c.first_outcome_difference !== c.divergence.index && (
              <>; the first difference in outcome is at step {c.first_outcome_difference + 1}</>
            )}
          {c.first_outcome_difference === c.divergence.index && (
            <> (which is also where the outcome first differs)</>
          )}
          .
        </p>
      )}
      <section aria-labelledby={ids[0]}>
        <h2 id={ids[0]}>Summary</h2>
        <Summary c={c} />
      </section>
      <section aria-labelledby={ids[1]}>
        <h2 id={ids[1]}>Steps</h2>
        <AlignedSteps
          rows={c.steps}
          divergence={c.divergence?.index ?? null}
          outcome={c.first_outcome_difference}
        />
      </section>
      <section aria-labelledby={ids[2]}>
        <h2 id={ids[2]}>Evaluations</h2>
        <Evaluations c={c} />
      </section>
      <section aria-labelledby={ids[3]}>
        <h2 id={ids[3]}>Cost by model</h2>
        <CostByModel c={c} />
      </section>
      <section aria-labelledby={ids[4]}>
        <h2 id={ids[4]}>Files</h2>
        <Files c={c} />
      </section>
    </>
  );
}

/** The compare page: `?left=<run>&right=<run>`. Everything shown is the API's role-aware comparison. */
export function CompareView({ base }: { base: Base }) {
  const params = useSearchParams();
  const left = params.get("left");
  const right = params.get("right");
  const q = useComparison(left, right);
  return (
    <div className="compare" data-testid="compare">
      <h1>Compare runs</h1>
      {!left || !right ? (
        <Empty title="Pick two runs to compare">
          Choose two runs in the{" "}
          <Link href={`/w/${base.workspace}/projects/${base.project}/runs`}>runs list</Link>.
        </Empty>
      ) : q.isPending ? (
        <Loading label="Comparing runs" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <Loaded c={q.data} base={base} />
      )}
    </div>
  );
}
