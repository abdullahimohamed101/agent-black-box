import { beforeEach, describe, expect, it } from "vitest";
import { fixturePolicyReply, fixturePolicyWrite, resetPolicyFixtures } from "@/fixtures/policies";
import { STARTER_POLICY } from "@/lib/policy";

const PRJ = "prj_00000000000000000000000001";
const owner = { content: true };
const q = (s = "") => new URLSearchParams(s);
const apr = (n: number) => `apr_${String(n).padStart(26, "0")}`;
beforeEach(resetPolicyFixtures);

describe("the fixture policy API", () => {
  it("publishes with a base version: a stale one is POLICY_VERSION_CONFLICT with the current version", () => {
    const stale = fixturePolicyWrite(
      ["v1", "policies", "publish"],
      { document: STARTER_POLICY, project_id: PRJ, base_version: 2 },
      owner,
    )!;
    expect(stale.status).toBe(409);
    expect(JSON.stringify(stale.body)).toContain('"current_version":3');
    const ok = fixturePolicyWrite(
      ["v1", "policies", "publish"],
      { document: STARTER_POLICY, project_id: PRJ, base_version: 3, comment: "c" },
      owner,
    )!;
    expect(ok.status).toBe(200);
    expect((ok.body as { version: number }).version).toBe(4);
    const cur = fixturePolicyReply(["v1", "policies", "current"], q(`project_id=${PRJ}`), owner)!;
    expect((cur.body as { policy: { version: number } }).policy.version).toBe(4);
  });
  it("refuses an invalid draft with the issues, on publish and on dry run", () => {
    for (const what of ["publish", "dry-run"]) {
      const r = fixturePolicyWrite(
        ["v1", "policies", what],
        { document: "version: 3\n", project_id: PRJ },
        owner,
      )!;
      expect(r.status).toBe(422);
      expect(JSON.stringify(r.body)).toContain("POLICY_INVALID");
    }
  });
  it("dry run is deterministic and carries ids and counts only", () => {
    const run = () =>
      fixturePolicyWrite(
        ["v1", "policies", "dry-run"],
        { document: STARTER_POLICY, project_id: PRJ },
        owner,
      )!.body;
    expect(run()).toEqual(run());
    const body = run() as { samples: Record<string, unknown>[] };
    for (const s of body.samples)
      expect(Object.keys(s).sort()).toEqual([
        "capability",
        "decision",
        "event_id",
        "risk_class",
        "rule_id",
        "run_id",
      ]);
  });
  it("approvals move state in memory; a second decision is a 409 with the state", () => {
    const first = fixturePolicyWrite(
      ["v1", "approvals", apr(3), "decision"],
      { decision: "deny" },
      owner,
    )!;
    expect(first.status).toBe(200);
    expect(
      (
        fixturePolicyReply(["v1", "approvals", "pending-count"], q(), owner)!.body as {
          count: number;
        }
      ).count,
    ).toBe(5);
    const again = fixturePolicyWrite(
      ["v1", "approvals", apr(3), "decision"],
      { decision: "approve" },
      owner,
    )!;
    expect(again.status).toBe(409);
    expect(JSON.stringify(again.body)).toContain('"state":"DENIED"');
  });
  it("the viewer gets 403 everywhere", () => {
    const viewer = { content: false };
    expect(fixturePolicyReply(["v1", "approvals"], q(), viewer)!.status).toBe(403);
    expect(
      fixturePolicyWrite(["v1", "approvals", apr(1), "decision"], { decision: "approve" }, viewer)!
        .status,
    ).toBe(403);
    expect(fixturePolicyReply(["v1", "runs"], q(), viewer)).toBeNull();
  });
});
