import type { FacetsOut } from "@/lib/api/types";
import { FACET_FIELDS } from "@/lib/search";
import { statusLabel } from "./StatusBadge";

const TITLES: Record<keyof typeof FACET_FIELDS, string> = {
  status: "Status",
  agents: "Agents",
  tools: "Tools",
  models: "Models",
  event_types: "Event types",
};

/** Counts over the newest matching runs; a click appends one more term, and the server re-runs the search. */
export function Facets({
  facets,
  skipped,
  onAdd,
}: {
  facets: FacetsOut | null | undefined;
  skipped?: string | null;
  onAdd: (field: string, value: string) => void;
}) {
  if (!facets)
    return skipped ? (
      <p className="muted" data-testid="facets-skipped">
        Facets were left out to keep this search fast. Narrow the query to see them.
      </p>
    ) : null;
  const keys = (Object.keys(FACET_FIELDS) as (keyof typeof FACET_FIELDS)[]).filter(
    (k) => facets[k].length > 0,
  );
  if (keys.length === 0) return null;
  return (
    <section className="facets" aria-label="Narrow the results">
      {keys.map((k) => (
        <div key={k} className="facet">
          <h3>{TITLES[k]}</h3>
          <ul>
            {facets[k].map((v) => (
              <li key={v.value}>
                {v.value.includes("*") ? (
                  <span>
                    {v.value} <span className="muted">({v.count})</span>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="facet-button"
                    onClick={() => onAdd(FACET_FIELDS[k], v.value)}
                  >
                    {k === "status" ? statusLabel(v.value as never) : v.value}{" "}
                    <span className="muted">({v.count})</span>
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
