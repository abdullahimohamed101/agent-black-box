"use client";

export type RunView = "timeline" | "waterfall" | "evidence";

const LABEL: Record<RunView, string> = {
  timeline: "Timeline",
  waterfall: "Waterfall",
  evidence: "Evidence",
};

/**
 * The run's views, drawn as tabs but built from native radio buttons: arrow keys and Space work and screen readers
 * announce a group of options without extra code. The input fills its label at zero opacity so the label can be styled
 * as the active tab while the input stays a real, clickable control.
 */
export function ViewSwitch({
  view,
  onChange,
  counts,
}: {
  view: RunView;
  onChange: (view: RunView) => void;
  /** Shown beside the tab name. Decoration only (aria-hidden), so each radio keeps its plain name. */
  counts?: Partial<Record<RunView, string | undefined>>;
}) {
  return (
    <fieldset className="seg">
      <legend className="sr-only">View</legend>
      {(Object.keys(LABEL) as RunView[]).map((v) => (
        <label key={v} className="seg-item">
          <input
            type="radio"
            name="run-view"
            value={v}
            checked={view === v}
            onChange={() => onChange(v)}
          />
          {LABEL[v]}
          {counts?.[v] && (
            <span className="seg-count" aria-hidden="true">
              {counts[v]}
            </span>
          )}
        </label>
      ))}
    </fieldset>
  );
}
