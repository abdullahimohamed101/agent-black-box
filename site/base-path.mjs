// The deploy path, in one place: next.config.ts (basePath, assetPrefix) and the metadata links (Next does not prefix the
// hrefs of public/ files, so the layout does it). ABB_SITE_BASE_PATH="" builds for a domain root (check-site.sh does both).
const raw = process.env.ABB_SITE_BASE_PATH ?? "/agent-black-box";
export const BASE_PATH = raw === "" || raw === "/" ? "" : "/" + raw.replace(/^\/+|\/+$/g, "");
