"use client";

import { useWasteReport, type WindowDays } from "@/lib/queries";
import { formatCost, formatInt } from "@/lib/format";
import { BarList } from "./charts";
import { Empty, ErrorState, Loading } from "./States";
import "../app/analytics.css";

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);

/**
 * "Where the money was wasted" (Phase 22 D12): spend on runs that failed, timed out or were blocked, plus retry spend in
 * the runs that did not, so no dollar counts twice. Every figure is the cost engine's (`/v1/analytics/waste`).
 */
export function WastePanel({
  project,
  days,
}: {
  project: string | null | undefined;
  days: WindowDays;
}) {
  const q = useWasteReport(project, days);
  const w = q.data;
  return (
    <section aria-labelledby="waste-h" className="waste" data-testid="waste-panel">
      <h2 id="waste-h">Where the money was wasted</h2>
      {q.isPending ? (
        <Loading label="Loading wasted spend" />
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : w ? (
        <div
          aria-busy={q.isPlaceholderData ? "true" : undefined}
          data-stale={q.isPlaceholderData ? "true" : undefined}
        >
          {w.wasted_usd === 0 ? (
            <Empty title="Nothing was wasted in this window" motif="tape">
              No run failed or retried with a cost.
            </Empty>
          ) : (
            <>
              <p className="waste-line" data-testid="waste-line">
                <span className="waste-total">{formatCost(w.wasted_usd)}</span>
                <span className="muted"> wasted</span>
                <span data-fig="share">
                  <span className="muted">of spend</span> {pct(w.wasted_share)}
                </span>
                <span data-fig="failed">
                  <span className="muted">failed runs</span> {formatCost(w.failed.spend_usd)} (
                  {formatInt(w.failed.runs)})
                </span>
                <span data-fig="retries">
                  <span className="muted">retries</span> {formatCost(w.retries.in_other_runs_usd)}
                </span>
                <span data-fig="after">
                  <span className="muted">after the first error</span>{" "}
                  {formatCost(w.failed.after_first_error_usd)}
                </span>
              </p>
              <p className="muted waste-note">
                Spend on runs that failed, timed out or were blocked, plus retries in the runs that
                did not. Retries inside a failed run are already in its spend.
              </p>
              <div className="two-col">
                <div>
                  <h3>By agent</h3>
                  <BarList
                    caption="Wasted spend by agent"
                    bars={w.by_agent.map((a) => ({
                      label: a.agent,
                      value: a.wasted_usd,
                      display: formatCost(a.wasted_usd),
                      note: `failed ${formatCost(a.failed_usd)} · retries ${formatCost(a.retry_usd)}`,
                    }))}
                  />
                  {w.by_agent_other && (
                    <p className="muted">
                      and {w.by_agent_other.groups} more agents:{" "}
                      {formatCost(w.by_agent_other.wasted_usd)}
                    </p>
                  )}
                </div>
                <div>
                  <h3>By model</h3>
                  <BarList
                    caption="Wasted spend by model"
                    bars={w.by_model.map((m) => ({
                      label: m.model ?? "(unknown model)",
                      value: m.wasted_usd,
                      display: formatCost(m.wasted_usd),
                      note: m.unpriced_calls
                        ? `${formatInt(m.unpriced_calls)} unpriced`
                        : `${formatInt(m.calls)} calls`,
                    }))}
                  />
                  {w.by_model_other && (
                    <p className="muted">
                      and {w.by_model_other.groups} more models:{" "}
                      {formatCost(w.by_model_other.wasted_usd)}
                    </p>
                  )}
                </div>
              </div>
              {w.failed.unpriced_calls > 0 && (
                <p className="muted" data-testid="waste-unpriced">
                  {formatInt(w.failed.unpriced_calls)} model calls in failed runs had no price and
                  count as $0, so the wasted figure may be higher.
                </p>
              )}
              {w.failed.after_first_error_unknown_runs > 0 && (
                <p className="muted" data-testid="waste-unknown">
                  {formatInt(w.failed.after_first_error_unknown_runs)} failed runs were summarised
                  before first-error facts existed, so their spend after the first error is not
                  counted.
                </p>
              )}
              {w.unrebuilt_runs > 0 && (
                <p className="muted" data-testid="waste-unrebuilt">
                  {formatInt(w.unrebuilt_runs)} {w.unrebuilt_runs === 1 ? "run was" : "runs were"}{" "}
                  summarised by an older version and lack first-error facts; rebuild them to
                  complete these figures.
                </p>
              )}
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
