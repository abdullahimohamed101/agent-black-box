"use client";

import { useMemo, useState } from "react";
import { diffPath, diffStats, parseDiff } from "@/lib/diff";
import { languageFor, tokenize, type Lang } from "@/lib/highlight";

const MAX_LINES = 2000;

function Code({ text, lang }: { text: string; lang: Lang }) {
  // Each token is a React text child: escaped by React, never parsed as HTML (ADR-032).
  return (
    <>
      {tokenize(text, lang).map((t, i) => (
        <span key={i} className={`tok-${t.type}`}>
          {t.text}
        </span>
      ))}
    </>
  );
}

/** A unified diff with line numbers and syntax colouring. Added and removed lines also carry +/- signs (not colour alone). */
export function DiffView({ text, lang }: { text: string; lang?: string | null }) {
  const files = useMemo(() => parseDiff(text), [text]);
  const [all, setAll] = useState(false);
  const stats = diffStats(files);
  const total = files.reduce((n, f) => n + f.lines.length, 0);
  // How many lines each file may show so that all files together stay within the cap.
  const caps = useMemo(() => {
    const limit = all ? Infinity : MAX_LINES;
    return files.map((f, i) =>
      Math.min(
        f.lines.length,
        Math.max(0, limit - files.slice(0, i).reduce((n, g) => n + g.lines.length, 0)),
      ),
    );
  }, [files, all]);
  return (
    <div className="diff" data-testid="diff">
      <p className="muted diff-stat">
        <span className="diff-add-n">+{stats.added}</span>{" "}
        <span className="diff-del-n">-{stats.removed}</span>
      </p>
      {files.map((f, fi) => {
        const path = diffPath(f);
        const language = languageFor(path, fi === 0 ? lang : null);
        const lines = f.lines.slice(0, caps[fi]);
        return (
          <div key={fi} className="diff-file">
            {path && <div className="diff-path">{path}</div>}
            <div role="group" aria-label={path ? `Diff of ${path}` : "Diff"} tabIndex={0}>
              {lines.map((l, i) => (
                <div key={i} className={`diff-line diff-${l.kind}`} data-kind={l.kind}>
                  <span className="diff-no" aria-hidden="true">
                    {l.oldNo ?? ""}
                  </span>
                  <span className="diff-no" aria-hidden="true">
                    {l.newNo ?? ""}
                  </span>
                  <span
                    className="diff-sign"
                    aria-label={
                      l.kind === "add" ? "added" : l.kind === "del" ? "removed" : undefined
                    }
                  >
                    {l.kind === "add" ? "+" : l.kind === "del" ? "-" : l.kind === "meta" ? "" : " "}
                  </span>
                  <span className="diff-text">
                    {l.kind === "meta" ? l.text : <Code text={l.text} lang={language} />}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
      {!all && total > MAX_LINES && (
        <p>
          <button type="button" onClick={() => setAll(true)}>
            Show all {total.toLocaleString("en-US")} lines
          </button>
        </p>
      )}
      {files.length === 0 && <p className="muted">(empty diff)</p>}
    </div>
  );
}
