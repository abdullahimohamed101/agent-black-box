"use client";

export type RunView = "timeline" | "waterfall";

/** Timeline or waterfall, as native radio buttons so the keyboard and screen readers work without extra code. */
export function ViewSwitch({
  view,
  onChange,
}: {
  view: RunView;
  onChange: (view: RunView) => void;
}) {
  return (
    <fieldset className="view-switch">
      <legend>View</legend>
      {(["timeline", "waterfall"] as const).map((v) => (
        <label key={v} className="chip">
          <input
            type="radio"
            name="run-view"
            value={v}
            checked={view === v}
            onChange={() => onChange(v)}
          />
          {v === "timeline" ? "Timeline" : "Waterfall"}
        </label>
      ))}
    </fieldset>
  );
}
