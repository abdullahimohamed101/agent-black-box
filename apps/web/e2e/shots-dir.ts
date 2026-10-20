import path from "node:path";

/**
 * Where an e2e spec writes its evidence screenshots (KI-123).
 *
 * `docs/screenshots/phase-*` is tracked history: a normal run must never rewrite it. Without `SHOTS_DIR` the pictures land in
 * a scratch folder under test-results/ (git-ignored); with `SHOTS_DIR` set they land under that directory, which is how a
 * deliberate refresh is done (`SHOTS_DIR=../../docs/screenshots pnpm test:e2e ...`). `rel` is the folder below the root, e.g.
 * "phase-4" or "phase-22/step-8". The result is relative to the Playwright working directory (apps/web) unless SHOTS_DIR is
 * absolute, so `${shotsDir("phase-4")}/x.png` works in the specs as before.
 */
export function shotsDir(
  rel: string,
  env: Record<string, string | undefined> = process.env,
): string {
  const root = env.SHOTS_DIR;
  return root ? path.join(root, rel) : path.join("test-results", "shots", rel);
}
