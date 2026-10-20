import { RELEASE_CLAIMS, repoFile } from "../config";

/** Section 8 (#privacy): objections answered as facts, with the limit in the same breath. */
export function Privacy() {
  return (
    <section className="band" id="privacy" aria-labelledby="privacy-h">
      <div className="wrap">
        <p className="eyebrow">Privacy</p>
        <h2 className="h2" id="privacy-h">What stays on your machine.</h2>
        <div className="privacy">
          <div>
            <h3 className="h3">Redacted before it is stored</h3>
            <p>Secrets are redacted before anything is stored. Capture is metadata-only by default; prompts and file contents are kept only if you opt in.</p>
          </div>
          <div>
            <h3 className="h3">Nothing third-party on this page</h3>
            <p>This page loads nothing from another origin; its own check records every request and fails on any other. The web app is served from your own origin under a Content-Security-Policy.</p>
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
