import { RELEASE_CLAIMS, repoFile } from "../config";
import { confirm } from "../lib/confirm";

/** Section 8 (#privacy): objections answered as facts, with the limit in the same breath. */
export function Privacy() {
  return (
    <section className="band" id="privacy" aria-labelledby="privacy-h">
      <div className="wrap">
        <p className="eyebrow">Privacy</p>
        <h2 className="h2" id="privacy-h">What stays on your machine.</h2>
        <div className="privacy">
          <div {...confirm("AFTER STEP 10: the planted-secret end-to-end test is re-run against the final demo")}>
            <h3 className="h3">Redacted before it is stored</h3>
            <p>Secrets are redacted before anything is stored. Capture is metadata-only by default; prompts and file contents are kept only if you opt in.</p>
          </div>
          <div {...confirm("AFTER STEP 17: the product's request log (release-checklist.sh network) shows only same-origin hosts")}>
            <h3 className="h3">No request to a third party</h3>
            <p>The product makes none, and neither does this page: its own check records every request and fails on any other origin.</p>
          </div>
          <div>
            <h3 className="h3">PostgreSQL and nothing else</h3>
            <p>You run PostgreSQL. There is no queue, cache or search service to operate.</p>
          </div>
        </div>
        <p className="limit">
          Stored events are never scrubbed retroactively; unknown-shaped secrets can pass in full capture mode.
          {RELEASE_CLAIMS ? (
            <>
              {" "}
              <a href={repoFile("docs/SECURITY.md")} rel="noopener">Read the security notes</a>.
            </>
          ) : null}
        </p>
      </div>
    </section>
  );
}
