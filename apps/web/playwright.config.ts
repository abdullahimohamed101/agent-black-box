import { defineConfig, devices } from "@playwright/test";

// Fixture server (port 3100) always; a real-API server per E2E script when its E2E_*_API_URL is set (see docs/TESTING.md).
// The web servers hold no credential: the specs sign in with a development session (E2E_SESSION_TOKEN, e2e/session.ts).
// Uses the system Chrome so no browser download is needed. Run `pnpm build` first.
const real = !!process.env.E2E_REAL_API_URL;
const stream = !!process.env.E2E_STREAM_API_URL;
const coding = !!process.env.E2E_CODING_API_URL;
const analytics = !!process.env.E2E_ANALYTICS_API_URL;
const auth = !!process.env.E2E_AUTH_API_URL;
const common = { reuseExistingServer: false, timeout: 60_000 } as const;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: true,
  reporter: [["list"]],
  use: { ...devices["Desktop Chrome"], channel: "chrome", baseURL: "http://localhost:3100" },
  webServer: [
    {
      ...common,
      command: "pnpm exec next start --port 3100",
      url: "http://localhost:3100",
      env: { ABB_WEB_DATA_SOURCE: "fixtures", ABB_WEB_ALLOW_FIXTURES: "1" },
    },
    ...(real
      ? [
          {
            ...common,
            command: "pnpm exec next start --port 3102",
            url: "http://localhost:3102",
            env: {
              ABB_WEB_ORIGIN: "http://localhost:3102",
              ABB_API_INTERNAL_URL: process.env.E2E_REAL_API_URL ?? "http://localhost:8110",
            },
          },
        ]
      : []),
    ...(stream
      ? [
          {
            ...common,
            command: "pnpm exec next start --port 3103",
            url: "http://localhost:3103",
            env: {
              ABB_WEB_ORIGIN: "http://localhost:3103",
              ABB_API_INTERNAL_URL: process.env.E2E_STREAM_API_URL ?? "http://localhost:8120",
            },
          },
        ]
      : []),
    ...(coding
      ? [
          {
            ...common,
            command: "pnpm exec next start --port 3140",
            url: "http://localhost:3140",
            env: {
              ABB_WEB_ORIGIN: "http://localhost:3140",
              ABB_API_INTERNAL_URL: process.env.E2E_CODING_API_URL ?? "http://localhost:8140",
            },
          },
        ]
      : []),
    ...(analytics
      ? [
          {
            ...common,
            command: "pnpm exec next start --port 3150",
            url: "http://localhost:3150",
            env: {
              ABB_WEB_ORIGIN: "http://localhost:3150",
              ABB_API_INTERNAL_URL: process.env.E2E_ANALYTICS_API_URL ?? "http://localhost:8150",
            },
          },
        ]
      : []),
    ...(auth
      ? [
          {
            ...common,
            command: "pnpm exec next start --port 3160",
            url: "http://localhost:3160",
            env: {
              ABB_WEB_ORIGIN: "http://localhost:3160",
              ABB_API_INTERNAL_URL: process.env.E2E_AUTH_API_URL!,
            },
          },
        ]
      : []),
  ],
});
