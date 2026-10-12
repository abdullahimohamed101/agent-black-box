"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { runPath, type Base } from "@/lib/routes";
import { TraceLine } from "./illustrations";
import { useCan } from "./WorkspaceProvider";
import "../app/setup-guide.css";

// The real package and import names (packages/sdk-python/pyproject.toml, docs/architecture/sdk-python.md). Whether the
// package is published to a public index is not verified here, so the checkout form is shown under it.
const INSTALL = "pip install agent-black-box";
const FROM_CHECKOUT = "pip install ./packages/sdk-python";
// `BlackBox()` reads BLACKBOX_API_KEY and BLACKBOX_ENDPOINT (default http://localhost:8000); an atexit hook flushes.
const SNIPPET = `from blackbox import BlackBox
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
        {state === "copied" ? "Copied" : "Copy"}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {state === "copied"
          ? "Copied"
          : state === "failed"
            ? "Could not copy; select the text"
            : ""}
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
        No runs yet
      </h2>
      <p className="setup-lede">
        Record one agent run in three steps. This page switches to it when the first event arrives.
      </p>
      <ol className="setup-steps">
        <li>
          <h3>Create an API key</h3>
          <p>
            {canCreateKey ? (
              <>
                <Link href={keysHref}>Create a key in settings</Link> and copy it at once: the token
                is shown only once.
              </>
            ) : (
              <>
                Ask a workspace owner or admin for a key (<Link href={keysHref}>API keys</Link>
                ). The token is shown only once, when it is created.
              </>
            )}
          </p>
        </li>
        <li>
          <h3>Install the SDK</h3>
          <CopyBlock text={INSTALL} label="Copy the install command" />
          <p className="muted">
            Working from a checkout of this repository? Run <code>{FROM_CHECKOUT}</code>.
          </p>
        </li>
        <li>
          <h3>Record a run</h3>
          <p>
            Set <code>BLACKBOX_API_KEY</code> to your key (and <code>BLACKBOX_ENDPOINT</code> if the
            API is not on <code>http://localhost:8000</code>), then run:
          </p>
          <CopyBlock text={SNIPPET} label="Copy the example code" />
        </li>
      </ol>
      {arrivedRunId ? (
        <div className="setup-status" role="status" data-testid="setup-arrived" data-tone="ok">
          <span>Your first run arrived.</span>
          <Link href={runPath(base, arrivedRunId)} className="button-link">
            Open the run
          </Link>
          {onDismiss && (
            <button type="button" onClick={onDismiss}>
              Show all runs
            </button>
          )}
        </div>
      ) : (
        <p className="setup-status" role="status" data-testid="setup-waiting">
          <span className="setup-dot" aria-hidden="true" />
          Waiting for your first event
        </p>
      )}
    </section>
  );
}
