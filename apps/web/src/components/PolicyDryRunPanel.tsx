"use client";

import Link from "next/link";
import { formatInt } from "@/lib/format";
import { TRUNCATED_TEXT, dryDecisionMeta, type DryRunOut } from "@/lib/policy";
import { OutcomeMark, RiskMark } from "./PolicyMarks";
import "../app/policy.css";

/**
 * What a draft would have done to the project's recorded runs. The API sends counts and ids only (no command, path or
 * argument), so this panel can show nothing else: samples link to the run and event, where the viewer's role decides what
 * content is visible.
 */
export function PolicyDryRunPanel({
  result,
  runHref,
}: {
  result: DryRunOut;
  runHref: (runId: string, eventId?: string) => string;
}) {
  const decisions = Object.entries(result.by_decision);
  return (
    <section className="pol-section pol-dry" aria-labelledby="dry-h" data-testid="dry-run-result">
      <h2 id="dry-h" tabIndex={-1}>
        Dry run result
      </h2>
      <p>
        Checked {formatInt(result.runs_considered)} recent run
        {result.runs_considered === 1 ? "" : "s"}: {formatInt(result.events_evaluated)} event
        {result.events_evaluated === 1 ? "" : "s"} evaluated, {formatInt(result.events_skipped)}{" "}
        skipped (not an action a policy covers). Nothing was changed.
      </p>
      {result.truncated && (
        <p role="note" className="notice" data-testid="dry-truncated">
          {TRUNCATED_TEXT[result.truncated] ?? "It stopped early."} The counts below are a floor.
        </p>
      )}
      <dl aria-label="Counts by decision">
        {decisions.map(([d, n]) => (
          <div key={d}>
            <dt>
              <OutcomeMark meta={dryDecisionMeta(d)} />
            </dt>
            <dd>{formatInt(n)}</dd>
          </div>
        ))}
      </dl>
      <div className="table-wrap">
        <table>
          <caption>By rule</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Decision</th>
              <th scope="col" className="num">
                Events
              </th>
            </tr>
          </thead>
          <tbody>
            {result.by_rule.map((r) => (
              <tr key={`${r.rule_id}|${r.decision}`}>
                <td>{r.rule_id ?? <span className="muted">no rule (policy default)</span>}</td>
                <td>
                  <OutcomeMark meta={dryDecisionMeta(r.decision)} />
                </td>
                <td className="num">{formatInt(r.count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.samples.length > 0 ? (
        <div className="table-wrap">
          <table>
            <caption>
              Sample actions that would be denied or need approval (up to{" "}
              {result.limits.max_samples})
            </caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Event</th>
                <th scope="col">Capability</th>
                <th scope="col">Risk</th>
                <th scope="col">Decision</th>
                <th scope="col">Rule</th>
              </tr>
            </thead>
            <tbody>
              {result.samples.map((s) => (
                <tr key={`${s.run_id}|${s.event_id}`} data-testid="dry-sample">
                  <td>
                    <Link href={runHref(s.run_id)}>…{s.run_id.slice(-8)}</Link>
                  </td>
                  <td>
                    <Link href={runHref(s.run_id, s.event_id)}>…{s.event_id.slice(-8)}</Link>
                  </td>
                  <td>
                    <code>{s.capability}</code>
                  </td>
                  <td>
                    <RiskMark risk={s.risk_class} />
                  </td>
                  <td>
                    <OutcomeMark meta={dryDecisionMeta(s.decision)} />
                  </td>
                  <td>{s.rule_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No action would be denied or need approval.</p>
      )}
    </section>
  );
}
