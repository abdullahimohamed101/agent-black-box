import { RELEASE_CLAIMS, REPO_URL, repoFile } from "../config";
import { NAME, STATUS_LINE } from "../content/copy";
import { Mark } from "./Mark";

export function Footer() {
  return (
    <footer className="foot">
      <div className="wrap">
        <div className="cols">
          <div>
            <a className="brand" href="#top">
              <Mark size={20} />
              <span>{NAME}</span>
            </a>
            <p className="status">{STATUS_LINE}</p>
          </div>
          <div>
            <h2 className="foot-h">Product</h2>
            <a href="#how">How it works</a>
            <a href="#screens">Screens</a>
            <a href="#quickstart">Run the demo</a>
          </div>
          <div>
            <h2 className="foot-h">Project</h2>
            {RELEASE_CLAIMS ? (
              <>
                <a href={REPO_URL} rel="noopener">Source on GitHub</a>
                <a href={repoFile("docs/README.md")} rel="noopener">Docs</a>
                <a href={repoFile("LICENSE")} rel="noopener">License</a>
              </>
            ) : (
              <span className="quiet">Apache-2.0</span>
            )}
          </div>
          {RELEASE_CLAIMS ? (
            <div>
              <h2 className="foot-h">Community</h2>
              <a href={repoFile("CONTRIBUTING.md")} rel="noopener">Contributing</a>
              <a href={repoFile("SECURITY.md")} rel="noopener">Security notes</a>
              <a href={repoFile("CODE_OF_CONDUCT.md")} rel="noopener">Code of conduct</a>
            </div>
          ) : null}
        </div>
        <p className="legal">Apache-2.0 · fonts IBM Plex under SIL OFL 1.1 · this page makes no third-party request</p>
      </div>
    </footer>
  );
}
