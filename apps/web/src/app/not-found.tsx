import Link from "next/link";
import { RecorderTape } from "@/components/illustrations";
import { Brand } from "@/components/shell/Brand";

// Phase 22 D9: not-found is an illustrated state, not a bare framework page.
export default function NotFound() {
  return (
    <main id="main" className="state-page" data-state="not-found">
      <Brand />
      <RecorderTape className="illus-lg" />
      <h1>This page isn&apos;t here</h1>
      <p className="muted">The link may be old, or the run or project may have been removed.</p>
      <p className="state-actions">
        <Link href="/" className="button-link">
          Go to your runs
        </Link>
      </p>
    </main>
  );
}
