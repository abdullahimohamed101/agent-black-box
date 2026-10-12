"use client";

import Link from "next/link";
import { useState } from "react";
import { ALL_STATUSES, type QueryTermOut, type RunStatus } from "@/lib/api/types";
import { useRuns, useSearchCatalogue } from "@/lib/queries";
import { AGENT_SLUG, appendTerm, effectiveQuery, removeSpan, searchProblem } from "@/lib/search";
import { COMPARE_PICKS, compareHref, toggleRun } from "@/lib/compare";
import { ATTENTION_STATUSES } from "@/lib/runsListRow";
import type { Base } from "@/lib/routes";
import { useProjectId } from "@/components/WorkspaceProvider";
import { RunsTable } from "./RunsTable";
import { statusLabel } from "./StatusBadge";
import { Empty, ErrorState, Loading } from "./States";
import { Facets } from "./Facets";
import { QueryChips } from "./QueryChips";
import { SearchBox, SearchProblemNotice } from "./SearchBox";
import { UnindexedNotice } from "./UnindexedNotice";

export type RangeKey = "1h" | "24h" | "7d" | "all";
export type ListFilters = { q: string; statuses: RunStatus[]; agent: string; range: RangeKey };
export const DEFAULT_FILTERS: ListFilters = { q: "", statuses: [], agent: "", range: "all" };
const RANGE_LABEL: Record<RangeKey, string> = {
  "1h": "Last hour",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  all: "All time",
};

export function RunsList({
  base,
  filters,
  onFilters,
  now: nowProp,
  pageSize = 50,
}: {
  base: Base;
  filters: ListFilters;
  onFilters: (f: ListFilters) => void;
  now?: number;
  pageSize?: number;
}) {
  // Relative times ("Last 24 hours") are `started:>=-24h` terms the server resolves against its own clock, so the
  // query key is stable and a bookmarked URL stays relative.
  const now = nowProp;
  // A half-typed agent slug would be a 422 from the API; only send valid ones.
  const agent = AGENT_SLUG.test(filters.agent) ? filters.agent : "";
  const project = useProjectId(base.project);
  const typed = filters.q.trim();
  const effective = effectiveQuery(typed, filters.range);
  const q = useRuns(
    {
      project,
      statuses: filters.statuses,
      agent,
      q: effective,
    },
    pageSize,
  );
  const catalogue = useSearchCatalogue(project);
  const [picked, setPicked] = useState<string[]>([]);
  // The tick boxes and the compare bar exist only while the person is comparing (plan D8).
  const [comparing, setComparing] = useState(false);
  const setCompare = (on: boolean) => {
    setComparing(on);
    if (!on) setPicked([]);
  };
  const href = compareHref(base, picked);
  const runs = q.data?.pages.flatMap((p) => p.items) ?? [];
  const first = q.data?.pages[0];
  const echo = q.isPlaceholderData ? null : (first?.query ?? null);
  const fields = echo?.fields ?? catalogue.data?.fields ?? null;
  const problem = q.isError && effective !== "" ? searchProblem(q.error) : null;
  const filtered =
    filters.q !== "" ||
    filters.statuses.length > 0 ||
    filters.agent !== "" ||
    filters.range !== "all";

  // A chip past the typed text is the range control's term: removing it resets the control.
  const removeTerm = (t: QueryTermOut) =>
    (t.span[0] ?? 0) >= typed.length
      ? onFilters({ ...filters, range: "all" })
      : onFilters({ ...filters, q: removeSpan(typed, t.span) });
  // A facet adds its term to the query, or takes it out again when it is already there.
  const toggleTerm = (term: string, existing: QueryTermOut | null) =>
    existing ? removeTerm(existing) : onFilters({ ...filters, q: appendTerm(typed, term) });
  const terms = effective === "" ? [] : (echo?.terms ?? null);
  // "Needs attention" is the legacy status parameter because the grammar cannot say "failed or blocked" (it ANDs).
  const attentionOn =
    filters.statuses.length === ATTENTION_STATUSES.length &&
    ATTENTION_STATUSES.every((s) => filters.statuses.includes(s));
  const toggleAttention = () =>
    onFilters({ ...filters, statuses: attentionOn ? [] : [...ATTENTION_STATUSES] });

  const toggle = (s: RunStatus) =>
    onFilters({
      ...filters,
      statuses: filters.statuses.includes(s)
        ? filters.statuses.filter((x) => x !== s)
        : [...filters.statuses, s],
    });

  const showTable = !q.isPending && !problem && !q.isError && runs.length > 0;

  return (
    <div className="runs">
      <div className="page-head">
        <h1>Runs</h1>
        {showTable && (
          <p className="muted" aria-live="polite">
            {runs.length} run{runs.length === 1 ? "" : "s"} loaded
            {q.hasNextPage ? "" : " (end of list)"}
          </p>
        )}
        <div className="page-actions">
          <button
            type="button"
            className="toggle"
            aria-pressed={attentionOn}
            onClick={toggleAttention}
            title="Failed, timed out and blocked runs"
          >
            Needs attention
          </button>
          <button
            type="button"
            className="toggle"
            aria-pressed={comparing}
            onClick={() => setCompare(!comparing)}
          >
            Compare
          </button>
        </div>
      </div>
      <SearchBox
        value={filters.q}
        fields={fields}
        onSubmit={(next) => onFilters({ ...filters, q: next })}
      />
      {echo && effective !== "" && (
        <QueryChips q={effective} terms={echo.terms} onRemove={removeTerm} />
      )}
      <form
        className="filters toolbar-row"
        role="search"
        aria-label="Filter runs"
        onSubmit={(e) => e.preventDefault()}
      >
        <fieldset>
          <legend>Status</legend>
          {ALL_STATUSES.map((s) => (
            <label key={s} className="chip">
              <input
                type="checkbox"
                checked={filters.statuses.includes(s)}
                onChange={() => toggle(s)}
              />
              {statusLabel(s)}
            </label>
          ))}
        </fieldset>
        <label>
          Agent
          <input
            type="text"
            value={filters.agent}
            placeholder="e.g. coding-agent"
            aria-invalid={filters.agent !== "" && agent === ""}
            onChange={(e) => onFilters({ ...filters, agent: e.target.value.trim() })}
          />
        </label>
        <label>
          Started
          <select
            value={filters.range}
            onChange={(e) => onFilters({ ...filters, range: e.target.value as RangeKey })}
          >
            {(Object.keys(RANGE_LABEL) as RangeKey[]).map((k) => (
              <option key={k} value={k}>
                {RANGE_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        {filtered && (
          <button type="button" onClick={() => onFilters(DEFAULT_FILTERS)}>
            Clear filters
          </button>
        )}
      </form>

      {q.isPending ? (
        <Loading label="Loading runs" />
      ) : problem ? (
        <SearchProblemNotice problem={problem} q={effective} onRetry={() => void q.refetch()} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="runs-body">
          <Facets
            facets={first?.facets}
            skipped={first?.facets_skipped}
            q={effective}
            terms={terms}
            onToggle={toggleTerm}
          />
          <div className="runs-main">
            {echo && <UnindexedNotice count={echo.unindexed} capped={echo.unindexed_capped} />}
            {runs.length === 0 ? (
              <Empty title={filtered ? "No runs match these filters" : "No runs yet"}>
                {filtered
                  ? "Try clearing a filter."
                  : "Runs appear here once an agent sends events."}
              </Empty>
            ) : (
              <>
                {comparing && (
                  <div className="compare-bar" data-testid="compare-bar">
                    <span role="status">
                      {picked.length === 0
                        ? `Tick ${COMPARE_PICKS} runs to compare them.`
                        : `${picked.length} of ${COMPARE_PICKS} runs picked.`}
                    </span>
                    {href ? (
                      <Link href={href} className="button-link" data-testid="compare-link">
                        Compare the two runs
                      </Link>
                    ) : (
                      picked.length === 1 && <span className="muted">Pick one more run.</span>
                    )}
                    {picked.length > 0 && (
                      <button type="button" onClick={() => setPicked([])}>
                        Clear picks
                      </button>
                    )}
                  </div>
                )}
                <RunsTable
                  runs={runs}
                  base={base}
                  caption="Runs"
                  now={now}
                  selected={picked}
                  onToggle={comparing ? (id) => setPicked((p) => toggleRun(p, id)) : undefined}
                />
                {q.hasNextPage && (
                  <button
                    type="button"
                    disabled={q.isFetchingNextPage}
                    onClick={() => void q.fetchNextPage()}
                  >
                    {q.isFetchingNextPage ? "Loading…" : "Load more"}
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
