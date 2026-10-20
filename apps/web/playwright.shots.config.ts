import { defineConfig, devices } from "@playwright/test";

// The release screenshots (scripts/screenshots.sh): an already-running, demo-seeded stack, no web server started here.
// One worker, in order: the specs share one boxes.json. Uses the system Chrome, like the other configs.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "release-shots.spec.ts",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    channel: "chrome",
    baseURL: process.env.E2E_SHOTS_BASE_URL ?? "http://localhost:3190",
  },
});
