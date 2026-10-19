import { readFileSync } from "node:fs";
import path from "node:path";
import { BASE_PATH } from "../base-path.mjs";
import { REPO_URL } from "../repo-url.mjs";

/**
 * The one build-time switch for every claim that waits for publication (Phase 20, gate G2): the words "open source" and
 * "self-hosted" as product claims, the "Source on GitHub" link and every other link into the repository (it 404s until the
 * repository is public), and the video caption "Open source. Self-hosted.". OFF by default. G2 flips it:
 *   ABB_RELEASE_CLAIMS=1 pnpm --filter @abb/site build
 * `scripts/check-site.sh claims` fails when an export built with the flag off contains any of them, and
 * `check-site.sh --release` fails while any [CONFIRM ...] marker remains in the output.
 */
export const RELEASE_CLAIMS = process.env.ABB_RELEASE_CLAIMS === "1";

export { BASE_PATH, REPO_URL };

/** URL of a file in public/ (Next prefixes bundled assets with the base path, not these). */
export const publicUrl = (p: string) => `${BASE_PATH}/${p.replace(/^\//, "")}`;

/** A path inside the repository, as a URL on the default branch. Only rendered when RELEASE_CLAIMS is on. */
export const repoFile = (p: string) => `${REPO_URL}/blob/main/${p}`;

/** A token's resolved value from the generated tokens.css (for the two `theme-color` metas, which cannot read CSS variables). */
export function tokenValue(name: string, scheme: "light" | "dark" = "light"): string {
  const css = readFileSync(path.join(process.cwd(), "src/styles/tokens.css"), "utf8");
  const root = scheme === "light" ? css.slice(0, css.indexOf("@media (prefers-color-scheme: dark)")) : css.slice(css.indexOf("@media (prefers-color-scheme: dark)"));
  const hex = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(root)?.[1];
  const ref = new RegExp(`${name}:\\s*var\\((--[a-z0-9-]+)\\)`).exec(root)?.[1];
  if (hex) return hex;
  if (ref) return tokenValue(ref, "light");
  throw new Error(`token ${name} not found in tokens.css`);
}
