import { RELEASE_CLAIMS, REPO_URL, repoFile } from "../config";
import { NAME } from "../content/copy";
import { Mark } from "./Mark";

/** Brand, three page anchors, and one outlined control: the repository once it is public (G2), the demo until then. */
export function TopBar() {
  return (
    <header className="top">
      <div className="wrap top-row">
        <a className="brand" href="#top" aria-label={`${NAME}, top of page`}>
          <Mark />
          <span>{NAME}</span>
        </a>
        <nav className="nav" aria-label="Page">
          <a href="#how">How it works</a>
          <a href="#screens">Screens</a>
          {RELEASE_CLAIMS ? (
            <>
              <a href="#quickstart">Run the demo</a>
              <a href={repoFile("docs/README.md")} rel="noopener">Docs</a>
              <a className="gh" href={REPO_URL} rel="noopener">Source on GitHub</a>
            </>
          ) : (
            <a className="gh" href="#quickstart">Run the demo</a>
          )}
        </nav>
      </div>
    </header>
  );
}
