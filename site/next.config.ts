import type { NextConfig } from "next";

// Static export for GitHub Pages under the project path (D16). Every URL the page emits is relative to that path, and the
// export also works at "/" (scripts/check-site.sh serves it both ways). ABB_SITE_BASE_PATH="" builds for a domain root.
const raw = process.env.ABB_SITE_BASE_PATH ?? "/agent-black-box";
const basePath = raw === "" || raw === "/" ? "" : "/" + raw.replace(/^\/+|\/+$/g, "");

const config: NextConfig = {
  output: "export",
  basePath,
  assetPrefix: basePath || undefined,
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
};

export default config;
