"use client";

import Link from "next/link";
import { FaultCut } from "@/components/illustrations";
import { Brand } from "@/components/shell/Brand";

// Phase 22 D9: the error boundary. It never prints the error's message (it can carry request details); the digest is the
// handle for the server log.
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main id="main" className="state-page" data-state="error">
      <Brand />
      <FaultCut className="illus-lg" />
      <h1>Something broke on this page</h1>
      <p className="muted" role="alert">
        Nothing you recorded was lost. Try again, and if it keeps happening tell whoever runs this
        workspace
        {error.digest ? ` (reference ${error.digest})` : ""}.
      </p>
      <p className="state-actions">
        <button type="button" onClick={reset}>
          Try again
        </button>
        <Link href="/">Go to your runs</Link>
      </p>
    </main>
  );
}
