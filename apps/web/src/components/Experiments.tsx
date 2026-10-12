"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { fieldErrors } from "@/lib/api/client";
import { KIND_LABEL } from "@/lib/evaluations";
import { formatTimestamp } from "@/lib/format";
import { useCreateExperiment, useEvaluators, useExperiments } from "@/lib/queries";
import { experimentPath, type Base } from "@/lib/routes";
import { useCan, useProjectId, useWorkspaceOptional } from "./WorkspaceProvider";
import { TableScroll } from "./TableScroll";
import { Empty, ErrorState, Loading } from "./States";
import { ActionError, FieldError } from "./SettingsKit";

function CreateForm({ base }: { base: Base }) {
  const id = useId();
  const create = useCreateExperiment();
  const evaluators = useEvaluators();
  const fixed = useProjectId(base.project);
  const projects = useWorkspaceOptional()?.projects ?? [];
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dataset, setDataset] = useState("");
  const [project, setProject] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const errors = fieldErrors(create.error);
  const projectId = fixed ?? (project || projects[0]?.id || "");
  const options = (evaluators.data ?? []).filter((e) => e.archived_at === null);
  const created = create.data;
  return (
    <form
      className="experiment-form"
      aria-label="New experiment"
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim() || !projectId) return;
        create.mutate({
          name: name.trim(),
          project_id: projectId,
          description: description.trim() || null,
          dataset_label: dataset.trim() || null,
          evaluator_ids: picked,
        });
      }}
    >
      <h2>New experiment</h2>
      <p className="muted">
        An experiment compares variants of one agent setup. Declare each variant&apos;s settings
        (model, prompt version); do not put secrets, prompts or paths in them. This product does not
        run the tasks: you attach runs you already recorded.
      </p>
      <div className="field">
        <label htmlFor={`${id}-name`}>Name</label>
        <input
          id={`${id}-name`}
          value={name}
          maxLength={128}
          required
          onChange={(e) => setName(e.target.value)}
        />
        <FieldError message={errors["name"]} />
      </div>
      {fixed === null && (
        <div className="field">
          <label htmlFor={`${id}-project`}>Project</label>
          <select
            id={`${id}-project`}
            value={projectId}
            onChange={(e) => setProject(e.target.value)}
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="field">
        <label htmlFor={`${id}-desc`}>Description (optional)</label>
        <textarea
          id={`${id}-desc`}
          value={description}
          maxLength={2000}
          rows={2}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-dataset`}>Task set label (optional)</label>
        <input
          id={`${id}-dataset`}
          value={dataset}
          maxLength={128}
          onChange={(e) => setDataset(e.target.value)}
        />
      </div>
      {options.length > 0 && (
        <fieldset>
          <legend>Evaluators used by &quot;Evaluate all&quot;</legend>
          {options.map((o) => (
            <label key={o.id} className="chip">
              <input
                type="checkbox"
                checked={picked.includes(o.id)}
                onChange={(e) =>
                  setPicked((p) => (e.target.checked ? [...p, o.id] : p.filter((x) => x !== o.id)))
                }
              />
              {o.name} ({KIND_LABEL[o.kind].toLowerCase()})
            </label>
          ))}
        </fieldset>
      )}
      <button type="submit" disabled={create.isPending || !name.trim() || !projectId}>
        {create.isPending ? "Creating…" : "Create experiment"}
      </button>
      <ActionError error={create.error} />
      {created && (
        <p role="status" data-testid="experiment-created">
          Created.{" "}
          <Link href={experimentPath(base, created.id)}>Open &quot;{created.name}&quot;</Link>
        </p>
      )}
    </form>
  );
}

/** The project's experiments. Names and descriptions are free text and are rendered as text only. */
export function Experiments({ base }: { base: Base }) {
  const list = useExperiments();
  const canWrite = useCan("experiment.write");
  return (
    <div className="experiments" data-testid="experiments">
      <h1>Experiments</h1>
      <p className="muted">
        Compare variants of an agent: the same task set run with one thing changed.
      </p>
      {list.isPending ? (
        <Loading label="Loading experiments" />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <Empty title="No experiments yet">
          {canWrite ? "Create one below." : "Nobody has created an experiment."}
        </Empty>
      ) : (
        <TableScroll label="Experiments">
          <table className="compare-table" data-testid="experiment-list">
            <thead>
              <tr>
                <th scope="col">Experiment</th>
                <th scope="col">Task set</th>
                <th scope="col">Variants</th>
                <th scope="col">Runs</th>
                <th scope="col">Created</th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((e) => (
                <tr key={e.id}>
                  <th scope="row">
                    <Link href={experimentPath(base, e.id)}>{e.name}</Link>
                    {e.description && <div className="muted">{e.description}</div>}
                  </th>
                  <td>{e.dataset_label ?? "—"}</td>
                  <td>{e.variant_count}</td>
                  <td>{e.run_count}</td>
                  <td>{formatTimestamp(e.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      {canWrite && <CreateForm base={base} />}
    </div>
  );
}
