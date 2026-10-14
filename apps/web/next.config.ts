import path from "node:path";
import type { NextConfig } from "next";
import { BASE_HEADERS } from "./src/server/csp";

// The Content-Security-Policy is per request (a nonce), so it is set in src/proxy.ts, with HSTS (which depends on
// ABB_WEB_ORIGIN at run time, not build time). The headers below are the static ones for every response, including
// the assets the proxy does not run for. ADR-155.
const config: NextConfig = {
  output: "standalone",
  // Monorepo: trace dependencies from the workspace root so the standalone bundle is complete.
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: Object.entries(BASE_HEADERS).map(([key, value]) => ({ key, value })),
      },
      { source: "/api/abb/:path*", headers: [{ key: "Cache-Control", value: "no-store" }] },
    ];
  },
};

export default config;
