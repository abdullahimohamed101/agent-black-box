"use client";

import Link from "next/link";
import { useState } from "react";
import { type QueryTermOut, type RunStatus } from "@/lib/api/types";
import { useRuns, useSearchCatalogue } from "@/lib/queries";
import {
  AGENT_SLUG,
  appendTerm,
  dropStatusTerms,
  effectiveQuery,
  isStatusTerm,
  removeSpan,
  searchProblem,
} from "@/lib/search";
import { COMPARE_PICKS, compareHref, toggleRun } from "@/lib/compare";
import { ATTENTION_STATUSES } from "@/lib/runsListRow";
import type { Base } from "@/lib/routes";
import { useProjectId } from "@/components/WorkspaceProvider";
import { runs as t } from "@/copy/runs";
import { RunsTable } from "./RunsTable";
import { RunsWeek } from "./RunsWeek";
import { statusLabel } from "./StatusBadge";
import { Empty, ErrorState, Loading } from "./States";
import { Facets } from "./Facets";
import { QueryChips, type ExtraChip } from "./QueryChips";
import { SearchBox, SearchProblemNotice } from "./SearchBox";
import { UnindexedNotice } from "./UnindexedNotice";
import { SetupGuide } from "./SetupGuide";

export type RangeKey = "1h" | "24h" | "7d" | "all";
export type ListFilters = { q: string; statuses: RunStatus[]; agent: string; range: RangeKey };
export const DEFAULT_FILTERS: ListFilters = { q: "", statuses: [], agent: "", range: "all" };

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
  // Turning a Status facet on also turns "Needs attention" off: they AND, and an empty list with no hint is worse.
  const toggleTerm = (term: string, existing: QueryTermOut | null) =>
    existing
      ? removeTerm(existing)
      : onFilters({
          ...filters,
          q: appendTerm(typed, term),
          statuses: isStatusTerm(term) ? [] : filters.statuses,
        });
  const terms = effective === "" ? [] : (echo?.terms ?? null);
  // "Needs attention" is the legacy status parameter because the grammar cannot say "failed or blocked" (it ANDs).
  const attentionOn =
    filters.statuses.length === ATTENTION_STATUSES.length &&
    ATTENTION_STATUSES.every((s) => filters.statuses.includes(s));
  const toggleAttention = () =>
    onFilters(
      attentionOn
        ? { ...filters, statuses: [] }
        : { ...filters, q: dropStatusTerms(typed), statuses: [...ATTENTION_STATUSES] },
    );

  // Filters that do not live in the query text still show as chips, so every active filter can be seen and removed: the
  // "Needs attention" status set (an older link or the preset) and an agent text that is not a valid slug.
  const extra: ExtraChip[] = [
    ...(filters.statuses.length > 0
      ? [
          {
            key: "statuses",
            text: `status: ${filters.statuses.map((s) => statusLabel(s).toLowerCase()).join(", ")}`,
            onRemove: () => onFilters({ ...filters, statuses: [] }),
          },
        ]
      : []),
    ...(filters.agent !== ""
      ? [
          {
            key: "agent",
            text: `agent: ${filters.agent}`,
            note: agent === "" ? t.search.badAgent : undefined,
            onRemove: () => onFilters({ ...filters, agent: "" }),
          },
        ]
      : []),
  ];

  // The first-run guide (plan D9) stays on screen once shown, so when the list's own poll brings the first run it can
  // say so and offer the run, instead of the page changing under the person.
  const [guideShown, setGuideShown] = useState(false);
  const [guideDismissed, setGuideDismissed] = useState(false);
  // A placeholder is the previous filter's answer, not this project's: never conclude "no runs yet" from it.
  const firstRunGuide =
    !q.isPending &&
    !q.isPlaceholderData &&
    !problem &&
    !q.isError &&
    runs.length === 0 &&
    !filtered;
  if (firstRunGuide && !guideShown) setGuideShown(true);
  // Once the person filters, they have moved past the guide: clearing the filter must not bring it back (review P3-4).
  if (guideShown && filtered && !q.isPlaceholderData) setGuideShown(false);
  const guideArrived =
    guideShown && !guideDismissed && !q.isPlaceholderData && !filtered && runs.length > 0;

  const guideMode = firstRunGuide || guideArrived;

  const showTable = !q.isPending && !problem && !q.isError && runs.length > 0;

  return (
    <div className="runs runs-home">
      <div className="page-title">
        <h1>{t.title}</h1>
        <button
          type="button"
          className="toggle"
          aria-pressed={comparing}
          onClick={() => setCompare(!comparing)}
        >
          {t.compare}
        </button>
      </div>
      <RunsWeek base={base} />
      <SearchBox
        value={filters.q}
        fields={fields}
        onSubmit={(next) => onFilters({ ...filters, q: next })}
      />
      <div className="chip-row">
        {((echo && effective !== "") || extra.length > 0) && (
          <QueryChips
            q={effective}
            terms={echo && effective !== "" ? echo.terms : []}
            onRemove={removeTerm}
            extra={extra}
          />
        )}
        <div className="chip-row-end">
          {showTable && (
            <span className="muted" aria-live="polite">
              {t.count(runs.length, !q.hasNextPage)}
            </span>
          )}
          {filtered && (
            <button
              type="button"
              className="link-button"
              onClick={() => onFilters(DEFAULT_FILTERS)}
            >
              {t.search.clear}
            </button>
          )}
        </div>
      </div>

      {q.isPending ? (
        <Loading label={t.loading} />
      ) : problem ? (
        <SearchProblemNotice problem={problem} q={effective} onRetry={() => void q.refetch()} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : guideMode ? (
        <SetupGuide
          base={base}
          arrivedRunId={guideArrived ? runs[0]!.id : null}
          onDismiss={() => setGuideDismissed(true)}
        />
      ) : (
        <div className="runs-body">
          <Facets
            facets={first?.facets}
            skipped={first?.facets_skipped}
            q={effective}
            terms={terms}
            onToggle={toggleTerm}
            attention={{ on: attentionOn, toggle: toggleAttention }}
          />
          <div className="runs-main">
            {echo && <UnindexedNotice count={echo.unindexed} capped={echo.unindexed_capped} />}
            {runs.length === 0 ? (
              <Empty
                title={t.empty.noMatchTitle}
                motif="none"
                action={
                  <button type="button" onClick={() => onFilters(DEFAULT_FILTERS)}>
                    {t.search.clear}
                  </button>
                }
              >
                {t.empty.noMatchBody}
              </Empty>
            ) : (
              <>
                {comparing && (
                  <div className="compare-bar" data-testid="compare-bar">
                    <span role="status">
                      {picked.length === 0
                        ? t.compareBar.none(COMPARE_PICKS)
                        : t.compareBar.some(picked.length, COMPARE_PICKS)}
                    </span>
                    {href ? (
                      <Link href={href} className="button-link" data-testid="compare-link">
                        {t.compareBar.go}
                      </Link>
                    ) : (
                      picked.length === 1 && <span className="muted">{t.compareBar.one}</span>
                    )}
                    {picked.length > 0 && (
                      <button type="button" onClick={() => setPicked([])}>
                        {t.compareBar.clear}
                      </button>
                    )}
                  </div>
                )}
                <RunsTable
                  runs={runs}
                  base={base}
                  caption={t.table.caption}
                  now={now}
                  selected={picked}
                  onToggle={comparing ? (id) => setPicked((p) => toggleRun(p, id)) : undefined}
                />
                {q.hasNextPage && (
                  <button
                    type="button"
                    className="load-more"
                    disabled={q.isFetchingNextPage}
                    onClick={() => void q.fetchNextPage()}
                  >
                    {q.isFetchingNextPage ? t.loadingMore : t.loadMore}
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
