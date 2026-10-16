"use client";

import { useState, useSyncExternalStore } from "react";
import type { FacetsOut, QueryTermOut, RunStatus } from "@/lib/api/types";
import { FACET_FIELDS, facetClickable, fieldTerm, findTerm } from "@/lib/search";
import { runs as t } from "@/copy/runs";
import { statusLabel } from "./StatusBadge";

const NARROW = "(max-width: 52rem)";
const isNarrow = () => window.matchMedia?.(NARROW).matches ?? false;
const subscribeNarrow = (notify: () => void) => {
  const m = window.matchMedia?.(NARROW);
  m?.addEventListener("change", notify);
  return () => m?.removeEventListener("change", notify);
};

type Item = {
  key: string;
  label: string;
  count?: number;
  /** The text this facet adds to (or removes from) the query. */
  term: string;
  match?: { field: string; value: string };
};
/** `plain`: a value no term can name (the agents overflow bucket `(other)`); shown, not clickable. */
type FacetItem = Item & { plain?: boolean };

/** Shown while the API has sent no counts (an empty query has no facets): the statuses people reach for first. */
const STATIC_STATUSES: RunStatus[] = [
  "FAILED",
  "TIMED_OUT",
  "BLOCKED",
  "RUNNING",
  "WAITING_FOR_APPROVAL",
  "SUCCESS",
  "CANCELLED",
];

/** Fixed terms the grammar can express. Findings are not searchable yet (the grammar reserves `finding:`), so this
 *  group is the nearest metadata: runs that errored or retried. */
const TIMES: Item[] = [
  { key: "t1h", label: "Last hour", term: "started:>=-1h" },
  { key: "t24h", label: "Last 24 hours", term: "started:>=-24h" },
  { key: "t7d", label: "Last 7 days", term: "started:>=-7d" },
];
const SIGNALS: Item[] = [
  { key: "err", label: "Has errors", term: "errors:>=1" },
  { key: "retry", label: "Retried", term: "retries:>=1" },
];

function fromFacet(k: keyof typeof FACET_FIELDS, facets: FacetsOut): FacetItem[] {
  return facets[k].map((v) => ({
    key: `${k}:${v.value}`,
    label: k === "status" ? statusLabel(v.value as RunStatus) : v.value,
    count: v.count,
    term: fieldTerm(FACET_FIELDS[k], v.value),
    match: { field: FACET_FIELDS[k], value: v.value },
    plain: !facetClickable(k, v.value),
  }));
}

/**
 * The facet column. Counts come from the search API (over the newest matching runs); the fixed groups are plain
 * grammar terms. A click edits the one query string: it adds the term, or removes it when it is already there.
 */
export function Facets({
  facets,
  skipped,
  q,
  terms,
  onToggle,
  attention,
}: {
  facets: FacetsOut | null | undefined;
  skipped?: string | null;
  /** The effective query text and the server's parse of it; null while a new query is loading. */
  q: string;
  terms: readonly QueryTermOut[] | null;
  /** Add the term text, or remove the echoed term that is already in the query. */
  onToggle: (term: string, existing: QueryTermOut | null) => void;
  /** The "Needs attention" preset: failed, timed-out and blocked runs together. It is a legacy status set, not a term. */
  attention?: { on: boolean; toggle: () => void };
}) {
  // On a phone the column folds into one "Narrow results" summary; on a wide screen the summary is hidden by CSS.
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, () => false);
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? !narrow;

  const statusItems: FacetItem[] =
    facets && facets.status.length > 0
      ? fromFacet("status", facets)
      : STATIC_STATUSES.map((s) => ({
          key: `s:${s}`,
          label: statusLabel(s),
          term: `status:${s.toLowerCase()}`,
          match: { field: "status", value: s },
        }));

  const groups: { title: string; items: FacetItem[] }[] = [
    { title: t.facets.status, items: statusItems },
    ...(facets
      ? [
          { title: t.facets.agent, items: fromFacet("agents", facets) },
          { title: t.facets.model, items: fromFacet("models", facets) },
        ]
      : []),
    { title: t.facets.started, items: TIMES },
    { title: t.facets.signals, items: SIGNALS },
  ].filter((g) => g.items.length > 0);
  const more: { title: string; items: FacetItem[] }[] = facets
    ? [
        { title: t.facets.tools, items: fromFacet("tools", facets) },
        { title: t.facets.eventTypes, items: fromFacet("event_types", facets) },
      ].filter((g) => g.items.length > 0)
    : [];

  const active = (i: FacetItem) => (terms ? findTerm(q, terms, i.term, i.match) : null);
  const activeCount = [...groups, ...more].reduce(
    (n, g) => n + g.items.filter((i) => active(i)).length,
    0,
  );

  const render = (g: { title: string; items: FacetItem[] }) => (
    <div key={g.title} className="facet">
      <h3>{g.title}</h3>
      <ul>
        {g.items.map((i) => {
          const on = active(i);
          return (
            <li key={i.key}>
              {i.plain ? (
                <span>
                  {i.label} <span className="muted">({i.count})</span>
                </span>
              ) : (
                <button
                  type="button"
                  className="facet-button"
                  aria-pressed={on !== null}
                  // A new query is loading: which terms are on is not known yet, so a click could not toggle right.
                  disabled={terms === null}
                  onClick={() => onToggle(i.term, on)}
                >
                  {i.label}
                  {i.count !== undefined && <span className="muted"> ({i.count})</span>}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );

  return (
    <section className="facets" aria-label={t.facets.label}>
      <details
        className="facets-box"
        open={open}
        onToggle={(e) => setChosen((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary>
          {t.facets.summary}
          {activeCount + (attention?.on ? 1 : 0) > 0 && (
            <span className="muted"> ({t.facets.on(activeCount + (attention?.on ? 1 : 0))})</span>
          )}
        </summary>
        {!facets && skipped && (
          <p className="muted" data-testid="facets-skipped">
            {t.facets.skipped}
          </p>
        )}
        {attention && (
          <div className="facet facet-preset">
            <button
              type="button"
              className="facet-button"
              aria-pressed={attention.on}
              title={t.facets.attentionHint}
              onClick={attention.toggle}
            >
              {t.facets.attention}
            </button>
          </div>
        )}
        {groups.map(render)}
        {more.length > 0 && (
          <details className="facets-more">
            <summary>{t.facets.more}</summary>
            {more.map(render)}
          </details>
        )}
      </details>
    </section>
  );
}
