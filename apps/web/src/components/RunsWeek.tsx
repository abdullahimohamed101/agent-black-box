"use client";

import Link from "next/link";
import { runs as t, weekParts, weekSentence } from "@/copy/runs";
import { states } from "@/copy/states";
import { useAnalyticsSummary } from "@/lib/queries";
import { analyticsPath, type Base } from "@/lib/routes";
import { useProjectId } from "./WorkspaceProvider";

const DAYS = 7;

/**
 * The last seven days in one sentence above the list: counts in words from the server's aggregate over the whole window
 * (KI-028), the same endpoint the old Overview used. It is silent while loading, when the request fails (the list has its
 * own error state) and when the window holds no runs (the setup guide or the empty list speaks instead).
 */
export function RunsWeek({ base }: { base: Base }) {
  const project = useProjectId(base.project);
  const stats = useAnalyticsSummary(project, DAYS);
  const d = stats.data;
  if (!d || d.runs.total === 0) return <p className="runs-lede" aria-hidden="true" />;
  const s = weekSentence(weekParts(d));
  return (
    <p
      className="runs-lede"
      data-testid="week-sentence"
      aria-busy={stats.isPlaceholderData || undefined}
    >
      <span>{s.lead}</span>
      {s.extra.map((x) => (
        <span key={x} className="opt">
          , {x}
        </span>
      ))}
      <span>, {s.failed}</span>
      {s.tail.map((x) => (
        <span key={x} className="opt">
          , {x}
        </span>
      ))}
      <span>.</span>{" "}
      <Link href={analyticsPath(base)} title={t.summary.analyticsHint}>
        {t.summary.analytics}
      </Link>
      {stats.isPlaceholderData && (
        <span role="status" className="sr-only">
          {states.updating}
        </span>
      )}
    </p>
  );
}
