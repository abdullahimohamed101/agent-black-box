"use client";

import { useId, useState } from "react";
import { fieldErrors } from "@/lib/api/client";
import type { EvaluatorOut } from "@/lib/api/types";
import { allowedLabels } from "@/lib/evaluations";
import { useRecordVerdict } from "@/lib/queries";
import { ActionError, FieldError } from "./SettingsKit";

const MAX_EXPLANATION = 4000; // the API's bound, shown so the counter is honest; the API validates

/** A person's verdict on a run. Shown only to people `/v1/me` lists `evaluation.label` for (the caller decides). */
export function VerdictForm({
  runId,
  evaluators,
}: {
  runId: string;
  evaluators: readonly EvaluatorOut[];
}) {
  const id = useId();
  const verdict = useRecordVerdict(runId);
  const [evaluatorId, setEvaluatorId] = useState(evaluators[0]?.id ?? "");
  const chosen = evaluators.find((e) => e.id === evaluatorId) ?? evaluators[0];
  const labels = chosen ? allowedLabels(chosen) : [];
  const [label, setLabel] = useState("");
  const [score, setScore] = useState("");
  const [explanation, setExplanation] = useState("");
  const errors = fieldErrors(verdict.error);

  if (!chosen) return null;
  const effectiveLabel = labels.includes(label) ? label : (labels[0] ?? "");
  const scoreNumber = score.trim() === "" ? null : Number(score);
  const scoreValid =
    scoreNumber === null || (Number.isFinite(scoreNumber) && scoreNumber >= 0 && scoreNumber <= 1);

  return (
    <form
      className="verdict-form"
      aria-label="Record a verdict"
      onSubmit={(e) => {
        e.preventDefault();
        if (!effectiveLabel || !scoreValid) return;
        verdict.mutate(
          {
            evaluator_id: chosen.id,
            label: effectiveLabel,
            score: scoreNumber,
            explanation: explanation.trim() === "" ? null : explanation,
          },
          { onSuccess: () => setExplanation("") },
        );
      }}
    >
      <h3>Record a verdict</h3>
      <div className="form-row">
        {evaluators.length > 1 && (
          <fieldset>
            <legend>Verdict</legend>
            {evaluators.map((e) => (
              <label key={e.id} className="chip">
                <input
                  type="radio"
                  name={`${id}-ev`}
                  value={e.id}
                  checked={e.id === chosen.id}
                  onChange={() => setEvaluatorId(e.id)}
                />
                {e.name}
              </label>
            ))}
          </fieldset>
        )}
        <fieldset aria-invalid={errors["label"] ? true : undefined}>
          <legend>Label</legend>
          {labels.map((l) => (
            <label key={l} className="chip">
              <input
                type="radio"
                name={`${id}-label`}
                value={l}
                checked={l === effectiveLabel}
                onChange={() => setLabel(l)}
              />
              {l}
            </label>
          ))}
          <FieldError message={errors["label"]} />
        </fieldset>
        <label htmlFor={`${id}-score`}>
          Score (0 to 1, optional)
          <input
            id={`${id}-score`}
            inputMode="decimal"
            value={score}
            onChange={(e) => setScore(e.target.value)}
            aria-invalid={!scoreValid || errors["score"] ? true : undefined}
          />
          <FieldError message={!scoreValid ? "Enter a number from 0 to 1." : errors["score"]} />
        </label>
      </div>
      <label htmlFor={`${id}-why`}>
        Explanation (optional)
        <textarea
          id={`${id}-why`}
          rows={3}
          maxLength={MAX_EXPLANATION}
          value={explanation}
          onChange={(e) => setExplanation(e.target.value)}
        />
        <FieldError message={errors["explanation"]} />
      </label>
      <button type="submit" disabled={verdict.isPending || !effectiveLabel || !scoreValid}>
        {verdict.isPending ? "Saving…" : "Save verdict"}
      </button>
      {verdict.isSuccess && (
        <span role="status" className="muted">
          {" "}
          Verdict saved.
        </span>
      )}
      <ActionError error={verdict.error} />
    </form>
  );
}
