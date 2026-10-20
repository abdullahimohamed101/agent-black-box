"use client";

import { useId, useState } from "react";
import type { EvaluationOut } from "@/lib/api/types";
import {
  JUDGE_BADGE,
  KIND_LABEL,
  STATE_META,
  formatScore,
  hasLive,
  isOpinion,
  judgeFacts,
  labelTone,
  runnable,
  splitEvaluations,
  verdictable,
} from "@/lib/evaluations";
import { ApiRequestError } from "@/lib/api/client";
import { useEvaluators, useRunEvaluations, useTriggerEvaluation } from "@/lib/queries";
import { formatTimestamp } from "@/lib/format";
import { useCan } from "./WorkspaceProvider";
import { Empty, ErrorState, Loading } from "./States";
import { ActionError } from "./SettingsKit";
import { VerdictForm } from "./VerdictForm";

function Row({ e, onShowEvent }: { e: EvaluationOut; onShowEvent?: (eventId: string) => void }) {
  const state = STATE_META[e.state];
  const facts = judgeFacts(e);
  const firstEvidence = e.evidence_event_ids[0];
  const titleId = useId();
  return (
    <li
      className="evaluation"
      data-testid="evaluation"
      data-state={e.state}
      data-kind={e.kind}
      aria-labelledby={titleId}
    >
      <div className="evaluation-head">
        <h4 id={titleId}>{e.evaluator_name}</h4>
        <span className="tag">{KIND_LABEL[e.kind]}</span>
        <span className="badge" data-tone={state.tone} data-state={e.state}>
          <span aria-hidden="true">{state.glyph}</span> {state.label}
        </span>
        {isOpinion(e) && (
          <span className="badge" data-tone="warn" data-testid="judge-badge">
            {JUDGE_BADGE}
          </span>
        )}
      </div>
      {e.state === "completed" && (
        <p className="evaluation-result">
          Score <strong>{formatScore(e.score)}</strong>
          {e.label && (
            <>
              {" "}
              <span className="badge" data-tone={labelTone(e.label)} data-testid="evaluation-label">
                {e.label}
              </span>
            </>
          )}
        </p>
      )}
      {e.state === "completed" && e.explanation && (
        <p className="evaluation-text" data-testid="explanation">
          {e.explanation}
        </p>
      )}
      {e.explanation_withheld && (
        <p className="muted" data-testid="explanation-withheld">
          Explanation withheld for your role.
        </p>
      )}
      {e.state === "failed" && (
        <p className="evaluation-error" data-testid="evaluation-error">
          Failed{e.error_code ? ` (${e.error_code})` : ""}
          {e.error ? `: ${e.error}` : ""}
        </p>
      )}
      {(e.state === "pending" || e.state === "running") && (
        <p className="muted">
          {e.state === "pending" ? "Waiting for a worker." : "Evaluating."} This list refreshes by
          itself.
        </p>
      )}
      {facts.length > 0 && (
        <dl className="fields evaluation-facts">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {e.metadata_withheld.length > 0 && (
        <p className="muted" data-testid="metadata-withheld">
          Some details are withheld for your role.
        </p>
      )}
      <p className="muted evaluation-foot">
        version {e.evaluator_version} · {formatTimestamp(e.completed_at ?? e.created_at)}
        {e.evidence_event_ids.length > 0 && <> · {e.evidence_event_ids.length} cited event(s)</>}
        {firstEvidence && onShowEvent && (
          <>
            {" · "}
            <button
              type="button"
              className="link-button"
              onClick={() => onShowEvent(firstEvidence)}
            >
              Show evidence
            </button>
          </>
        )}
      </p>
    </li>
  );
}

function StartEvaluator({ runId }: { runId: string }) {
  const id = useId();
  const evaluators = useEvaluators();
  const trigger = useTriggerEvaluation(runId);
  const [chosen, setChosen] = useState("");
  const options = runnable(evaluators.data ?? []);
  if (evaluators.isPending) return <Loading label="Loading evaluators" />;
  if (evaluators.isError)
    return <ErrorState error={evaluators.error} onRetry={() => void evaluators.refetch()} />;
  if (options.length === 0)
    return <p className="muted">No evaluators are defined for this workspace yet.</p>;
  const selected = options.some((o) => o.id === chosen) ? chosen : options[0]!.id;
  return (
    <form
      className="evaluation-start"
      aria-label="Start an evaluator"
      onSubmit={(ev) => {
        ev.preventDefault();
        trigger.mutate(selected);
      }}
    >
      <fieldset>
        <legend>Evaluator</legend>
        {options.map((o) => (
          <label key={o.id} className="chip">
            <input
              type="radio"
              name={id}
              value={o.id}
              checked={o.id === selected}
              onChange={() => setChosen(o.id)}
            />
            {o.name} ({KIND_LABEL[o.kind].toLowerCase()})
          </label>
        ))}
      </fieldset>
      <button type="submit" disabled={trigger.isPending}>
        {trigger.isPending ? "Starting…" : "Run evaluator"}
      </button>
      <ActionError error={trigger.error} />
    </form>
  );
}

/**
 * The run page's evaluations. A result is a derived record from a job, so it has its own state; the list polls while
 * a row is pending or running. Free text arrives nulled for readers without `payload.read` and is only ever rendered
 * as text. What a person may do (start an evaluator, record a verdict) follows `/v1/me`.
 */
export function Evaluations({
  runId,
  onShowEvent,
}: {
  runId: string;
  onShowEvent?: (eventId: string) => void;
}) {
  const list = useRunEvaluations(runId);
  const canRun = useCan("evaluation.run");
  const canLabel = useCan("evaluation.label");
  const evaluators = useEvaluators(canLabel);
  const heading = useId();
  const forbidden = list.error instanceof ApiRequestError && list.error.status === 403;
  const items = list.data?.items ?? [];
  const { latest, earlier } = splitEvaluations(items);
  return (
    <section aria-labelledby={heading} className="evaluations" data-testid="evaluations">
      <h2 id={heading}>Evaluations</h2>
      {list.isPending && <Loading label="Loading evaluations" />}
      {forbidden && (
        <p className="muted" data-state="forbidden">
          Evaluations are not available for your role.
        </p>
      )}
      {list.isError && !forbidden && (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      )}
      {list.isSuccess && (
        <>
          {hasLive(items) && (
            <p role="status" className="muted" data-testid="evaluations-live">
              Evaluations are running; results appear here as they finish.
            </p>
          )}
          {items.length === 0 ? (
            <Empty title="No evaluations yet">
              {canRun
                ? "Start an evaluator below to score this run."
                : "Nobody has evaluated this run."}
            </Empty>
          ) : (
            <ul className="evaluation-list" aria-label="Latest evaluations">
              {latest.map((e) => (
                <Row key={e.id} e={e} onShowEvent={onShowEvent} />
              ))}
            </ul>
          )}
          {earlier.length > 0 && (
            <details className="evaluation-history">
              <summary>Earlier results ({earlier.length})</summary>
              <ul className="evaluation-list" aria-label="Earlier evaluations">
                {earlier.map((e) => (
                  <Row key={e.id} e={e} onShowEvent={onShowEvent} />
                ))}
              </ul>
            </details>
          )}
          {list.data.truncated && (
            <p className="muted" data-testid="evaluations-truncated">
              Older evaluations are not shown.
            </p>
          )}
          {items.length > 0 && (
            <p className="muted">
              A model judge gives an opinion, not a fact: only rule, metric and human results are
              ground truth.
            </p>
          )}
        </>
      )}
      {canRun && <StartEvaluator runId={runId} />}
      {canLabel && evaluators.isSuccess && verdictable(evaluators.data).length > 0 && (
        <VerdictForm runId={runId} evaluators={verdictable(evaluators.data)} />
      )}
    </section>
  );
}
