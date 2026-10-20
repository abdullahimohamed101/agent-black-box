import { describe, expect, it } from "vitest";
import { shotsDir } from "../e2e/shots-dir";

describe("shotsDir", () => {
  it("defaults to a git-ignored scratch folder, never docs/screenshots", () => {
    expect(shotsDir("phase-4", {})).toBe("test-results/shots/phase-4");
    expect(shotsDir("phase-22/step-8", {})).toBe("test-results/shots/phase-22/step-8");
    expect(shotsDir("phase-4", { SHOTS_DIR: "" })).not.toContain("docs/screenshots");
  });

  it("writes under SHOTS_DIR for a deliberate refresh", () => {
    expect(shotsDir("phase-4", { SHOTS_DIR: "../../docs/screenshots" })).toBe(
      "../../docs/screenshots/phase-4",
    );
    expect(shotsDir("phase-22/step-8", { SHOTS_DIR: "/tmp/out" })).toBe("/tmp/out/phase-22/step-8");
  });
});
