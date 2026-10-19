import { RELEASE_CLAIMS, REPO_URL } from "../config";
import { FACTS, H1, SUB } from "../content/copy";
import type { ReactNode } from "react";

/** Headline, sub, the actions and the fold line. `children` is the trace (section 1's proof), rendered under them. */
export function Hero({ children }: { children?: ReactNode }) {
  return (
    <section className="hero" id="top" aria-labelledby="hero-h">
      <div className="wrap">
        <h1 className="hero-h" id="hero-h">{H1}</h1>
        <p className="lede">{SUB}</p>
        <div className="actions">
          <a className="btn btn-primary" href="#quickstart">Run the demo</a>
          {RELEASE_CLAIMS ? (
            <a className="btn btn-secondary" href={REPO_URL} rel="noopener">Source on GitHub</a>
          ) : null}
        </div>
        <p className="facts">
          {FACTS.map((f) => (
            <span key={f}>{f}</span>
          ))}
        </p>
        {children}
      </div>
    </section>
  );
}
