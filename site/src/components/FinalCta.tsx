import { RELEASE_CLAIMS, REPO_URL, repoFile } from "../config";
import { ONE_LINE } from "../content/commands";
import { CommandBlock } from "./CommandBlock";

/** Section 10: the safety net. The five commands as one line, no dark band (a tonal band would read as a second surface). */
export function FinalCta() {
  return (
    <section className="band final" id="start" aria-labelledby="final-h">
      <div className="wrap">
        <h2 className="h2" id="final-h">Run it on a failed run of your own.</h2>
        <CommandBlock id="cmd-final" label="shell, one line" lines={[[ONE_LINE]]} className="final-block" />
        <div className="actions">
          {RELEASE_CLAIMS ? (
            <>
              <a className="btn btn-primary" href={repoFile("docs/quickstart.md")} rel="noopener">Read the quickstart</a>
              <a className="btn btn-secondary" href={REPO_URL} rel="noopener">Source on GitHub</a>
            </>
          ) : (
            <a className="btn btn-primary" href="#quickstart">Read the quickstart</a>
          )}
        </div>
      </div>
    </section>
  );
}
