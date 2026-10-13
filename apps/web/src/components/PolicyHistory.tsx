"use client";

import { useState } from "react";
import { formatTimestamp } from "@/lib/format";
import { diffLines, type PolicyVersionOut } from "@/lib/policy";
import { usePolicyDiff, usePolicyVersion, usePolicyVersions } from "@/lib/policyApi";
import { PolicyEditor } from "./PolicyEditor";
import { ErrorState, Loading } from "./States";
import "../app/policy.css";

const KIND_CLASS = { add: "pol-add", del: "pol-del", hunk: "pol-hunk", head: "pol-head", ctx: "" };

/**
 * Version history of one scope: every published version, newest first. Selecting one shows its text read-only;
 * "Compare with current" shows the unified diff as text, added lines in bold ink and removed lines in amber (red is
 * for failed runs). Versions are immutable, so what is shown never changes after it is read.
 */
export function PolicyHistory({
  projectId,
  onUseAsDraft,
}: {
  projectId: string | null;
  /** Present only for a person who may edit; puts the version's text in the editor as the draft. */
  onUseAsDraft?: (text: string, version: number) => void;
}) {
  const versions = usePolicyVersions(projectId);
  const [picked, setPicked] = useState<number | null>(null);
  const [compare, setCompare] = useState(false);
  const items: PolicyVersionOut[] = versions.data?.pages.flatMap((p) => p.items) ?? [];
  const current = items[0]?.version ?? null;
  const detail = usePolicyVersion(projectId, picked);
  const diff = usePolicyDiff(projectId, compare ? picked : null, compare ? current : null);

  return (
    <section className="pol-section pol-versions" aria-labelledby="hist-h">
      <h2 id="hist-h">Version history</h2>
      {versions.isPending ? (
        <Loading label="Loading versions" />
      ) : versions.isError ? (
        <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
      ) : items.length === 0 ? (
        <p className="muted">Nothing has been published for this scope yet.</p>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Published versions, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Version</th>
                  <th scope="col">Published</th>
                  <th scope="col">By</th>
                  <th scope="col" className="num">
                    Rules
                  </th>
                  <th scope="col">Comment</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((v) => (
                  <tr
                    key={v.version}
                    data-testid="version-row"
                    data-current={v.version === current || undefined}
                    data-open={v.version === picked || undefined}
                  >
                    <td>
                      v{v.version}
                      {v.version === current && <span className="muted"> (current)</span>}
                    </td>
                    <td>{formatTimestamp(v.created_at)}</td>
                    <td>
                      <code>{v.created_by}</code>
                    </td>
                    <td className="num">{v.rule_count}</td>
                    <td className="cmt">{v.comment ?? <span className="muted">—</span>}</td>
                    <td>
                      <button
                        type="button"
                        aria-pressed={v.version === picked}
                        aria-label={`${v.version === picked ? "Hide" : "View"} version ${v.version}`}
                        onClick={() => {
                          setPicked(v.version === picked ? null : v.version);
                          setCompare(false);
                        }}
                      >
                        {v.version === picked ? "Hide" : "View"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {versions.hasNextPage && (
            <p>
              <button
                type="button"
                disabled={versions.isFetchingNextPage}
                onClick={() => void versions.fetchNextPage()}
              >
                {versions.isFetchingNextPage ? "Loading…" : "Load older versions"}
              </button>
            </p>
          )}
        </>
      )}
      {picked != null && (
        <div className="pol-viewer-wrap" data-testid="version-view">
          <h3>Version {picked}</h3>
          {detail.isPending ? (
            <Loading label="Loading this version" />
          ) : detail.isError ? (
            <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
          ) : (
            <>
              <PolicyEditor
                value={detail.data.document}
                readOnly
                label={`Policy text of version ${picked} (read only)`}
              />
              <p className="pol-actions">
                {picked !== current && (
                  <button
                    type="button"
                    aria-pressed={compare}
                    onClick={() => setCompare((c) => !c)}
                  >
                    {compare ? "Hide comparison" : `Compare with current (v${current})`}
                  </button>
                )}
                {onUseAsDraft && picked !== current && (
                  <button type="button" onClick={() => onUseAsDraft(detail.data.document, picked)}>
                    Use as draft
                  </button>
                )}
              </p>
              {compare &&
                (diff.isPending ? (
                  <Loading label="Loading the comparison" />
                ) : diff.isError ? (
                  <ErrorState error={diff.error} onRetry={() => void diff.refetch()} />
                ) : (
                  <>
                    <p className="muted">
                      Changes from v{diff.data.from_version} to v{diff.data.to_version}. Added lines
                      are bold, removed lines are amber.
                    </p>
                    {diff.data.truncated && (
                      <p role="note" className="notice" data-testid="diff-truncated">
                        The comparison was cut short. Open each version to read it in full.
                      </p>
                    )}
                    <pre
                      className="pol-diff"
                      tabIndex={0}
                      aria-label={`Changes from version ${diff.data.from_version} to version ${diff.data.to_version}`}
                      data-testid="policy-diff"
                    >
                      {diffLines(diff.data.diff).map((l, i) => (
                        <span key={i} className={KIND_CLASS[l.kind]} data-kind={l.kind}>
                          {l.text || " "}
                        </span>
                      ))}
                    </pre>
                  </>
                ))}
            </>
          )}
        </div>
      )}
    </section>
  );
}
