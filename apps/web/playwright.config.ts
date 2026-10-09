import { defineConfig, devices } from "@playwright/test";

// Fixture server (port 3100) always; real-API server (port 3102) when E2E_REAL_API_KEY is set (see docs/TESTING.md).
// Uses the system Chrome so no browser download is needed. Run `pnpm build` first.
const real = !!process.env.E2E_REAL_API_KEY;
const stream = !!process.env.E2E_STREAM_API_KEY;
const coding = !!process.env.E2E_CODING_API_KEY;
const analytics = !!process.env.E2E_ANALYTICS_API_KEY;
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
              ABB_WEB_API_KEY: process.env.E2E_REAL_API_KEY!,
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
              ABB_WEB_API_KEY: process.env.E2E_STREAM_API_KEY!,
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
              ABB_WEB_API_KEY: process.env.E2E_CODING_API_KEY!,
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
              ABB_WEB_API_KEY: process.env.E2E_ANALYTICS_API_KEY!,
              ABB_API_INTERNAL_URL: process.env.E2E_ANALYTICS_API_URL ?? "http://localhost:8150",
            },
          },
        ]
      : []),
  ],
});
