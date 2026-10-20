"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { runs as t } from "@/copy/runs";
import { runPath, type Base } from "@/lib/routes";
import { TraceLine } from "./illustrations";
import { useCan } from "./WorkspaceProvider";
import "../app/setup-guide.css";

// No package is published to a public index yet, so the only install line that works is the from-checkout one
// (docs/quickstart.md, "Trace your own agent"). Do not show a registry name here until it is published and checked.
const INSTALL = "pip install ./packages/sdk-python";
// `BlackBox()` reads BLACKBOX_API_KEY and BLACKBOX_ENDPOINT (default http://localhost:8000); an atexit hook flushes.
const SNIPPET = `from agent_blackbox import BlackBox
bb = BlackBox()
with bb.run("My first run"): pass`;

/** A code line with a copy button. The confirmation is announced through a polite live region. */
function CopyBlock({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2500);
  };
  return (
    <div className="setup-code">
      <pre>
        <code>{text}</code>
      </pre>
      <button type="button" onClick={() => void copy()} aria-label={label}>
        {state === "copied" ? t.guide.copied : t.guide.copy}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {state === "copied" ? t.guide.copied : state === "failed" ? t.guide.copyFailed : ""}
      </span>
    </div>
  );
}

/**
 * The first-run empty state (Phase 22 D9): three steps from nothing to a recorded run, then a live status line.
 * `arrivedRunId` is set by the caller (which already polls the runs list) when the first run shows up.
 */
export function SetupGuide({
  base,
  arrivedRunId,
  onDismiss,
}: {
  base: Base;
  arrivedRunId?: string | null;
  onDismiss?: () => void;
}) {
  const canCreateKey = useCan("api_key.create");
  const keysHref = `/w/${base.workspace}/settings/api-keys`;
  return (
    <section className="setup" aria-labelledby="setup-h" data-testid="setup-guide">
      <TraceLine className="illus-lg" />
      <h2 id="setup-h" className="setup-headline">
        {t.guide.title}
      </h2>
      <p className="setup-lede">{t.guide.lede}</p>
      <ol className="setup-steps">
        <li>
          <h3>{t.guide.keyTitle}</h3>
          <p>
            {canCreateKey ? (
              <>
                <Link href={keysHref}>{t.guide.keyAllowed}</Link> {t.guide.keyAllowedAfter}
              </>
            ) : (
              <>
                {t.guide.keyDeniedBefore}
                <Link href={keysHref}>{t.guide.keyDeniedLink}</Link>
                {t.guide.keyDeniedAfter}
              </>
            )}
          </p>
        </li>
        <li>
          <h3>{t.guide.installTitle}</h3>
          <CopyBlock text={INSTALL} label="Copy the install command" />
          <p className="muted">{t.guide.checkout}</p>
        </li>
        <li>
          <h3>{t.guide.recordTitle}</h3>
          <p>
            {t.guide.recordBefore} <code>BLACKBOX_API_KEY</code> {t.guide.recordMid}{" "}
            <code>BLACKBOX_ENDPOINT</code> {t.guide.recordMid2} <code>http://localhost:8000</code>
            {t.guide.recordAfter}
          </p>
          <CopyBlock text={SNIPPET} label="Copy the example code" />
        </li>
      </ol>
      {arrivedRunId ? (
        <div className="setup-status" role="status" data-testid="setup-arrived" data-tone="ok">
          <span>{t.guide.arrived}</span>
          <Link href={runPath(base, arrivedRunId)} className="button-link">
            {t.guide.open}
          </Link>
          {onDismiss && (
            <button type="button" onClick={onDismiss}>
              {t.guide.showAll}
            </button>
          )}
        </div>
      ) : (
        <p className="setup-status" role="status" data-testid="setup-waiting">
          <span className="setup-dot" aria-hidden="true" />
          {t.guide.waiting}
        </p>
      )}
    </section>
  );
}
