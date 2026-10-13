import { describe, expect, it } from "vitest";
import { fixtureValidate } from "@/fixtures/policies";
import type { EventOut } from "@/lib/api/types";
import { describe as describeEvent } from "@/lib/timeline";
import {
  STARTER_POLICY,
  ageSeconds,
  approvalStateMeta,
  approverText,
  decisionErrorText,
  diffLines,
  dryDecisionMeta,
  isPolicyService,
  leftText,
  offsetOf,
  parseApprovalTab,
  pendingApproval,
  policyEventLabel,
  riskMeta,
  tooLargeForWeb,
} from "@/lib/policy";

const ev = (event_type: string, attributes: Record<string, unknown> = {}, sdk?: object): EventOut =>
  ({
    event_id: "evt_1",
    event_type,
    attributes,
    sdk: sdk ?? null,
    occurred_at: "2026-10-13T12:00:00Z",
  }) as unknown as EventOut;

describe("policy event labels", () => {
  it("states the rule and its version for allow and deny, and the default when no rule matched", () => {
    const allowed = ev("policy.decision", {
      "policy.id": "allow-docs",
      "policy.version": 4,
      "policy.decision": "allow",
    });
    expect(policyEventLabel(allowed)?.word).toBe("Allowed by allow-docs v4");
    expect(
      policyEventLabel(
        ev("policy.decision", { "policy.id": "default", "policy.decision": "allow" }),
      )?.word,
    ).toBe("Allowed (default)");
    expect(policyEventLabel(ev("policy.decision", { "policy.decision": "allow" }))?.word).toBe(
      "Allowed (default)",
    );
    const denied = policyEventLabel(
      ev("policy.action.blocked", {
        "policy.id": "deny-env-read",
        "policy.version": 3,
        "policy.reason": "Environment files hold secrets.",
      }),
    );
    expect(denied?.word).toBe("Denied by deny-env-read v3");
    expect(denied?.detail).toBe("Environment files hold secrets.");
    expect(denied?.glyph).toBe("⊘");
  });

  it("labels every approval transition, and nothing else", () => {
    const words: [string, string][] = [
      ["approval.requested", "Approval requested"],
      ["approval.granted", "Approved"],
      ["approval.denied", "Denied"],
      ["approval.expired", "Approval expired"],
      ["approval.cancelled", "Approval cancelled"],
      ["approval.consumed", "Approval used"],
    ];
    for (const [type, word] of words) expect(policyEventLabel(ev(type))?.word, type).toBe(word);
    expect(policyEventLabel(ev("tool.call.started"))).toBeNull();
    expect(policyEventLabel(ev("policy.warning"))).toBeNull();
  });

  it("uses amber for waiting and expired, ink for outcomes, muted for cancelled; never a failure tone", () => {
    const tone = (t: string) => policyEventLabel(ev(t))?.tone;
    expect(tone("approval.requested")).toBe("wait");
    expect(tone("approval.expired")).toBe("wait");
    expect(tone("approval.denied")).toBe("ink");
    expect(tone("approval.granted")).toBe("ink");
    expect(tone("approval.cancelled")).toBe("muted");
  });

  it("describe() carries the label and the author's reason as text", () => {
    const e = ev("policy.action.blocked", {
      "policy.id": "r",
      "policy.version": 1,
      "policy.reason": "Because.",
    });
    expect(describeEvent(e)).toBe("Denied by r v1 · Because.");
  });

  it("recognises only the policy service as the writer", () => {
    expect(isPolicyService(ev("approval.requested", {}, { name: "abb-policy-service" }))).toBe(
      true,
    );
    expect(isPolicyService(ev("approval.requested", {}, { name: "blackbox" }))).toBe(false);
    expect(isPolicyService(ev("approval.requested"))).toBe(false);
  });

  it("finds what a run is waiting on, until a later decision closes it", () => {
    const req = ev("approval.requested", {
      "approval.id": "apr_1",
      "approval.capability": "github.push",
    });
    expect(pendingApproval([ev("run.started"), req])).toEqual({
      id: "apr_1",
      capability: "github.push",
    });
    expect(pendingApproval([req, ev("approval.granted")])).toBeNull();
    expect(pendingApproval([req, ev("approval.expired")])).toBeNull();
    expect(pendingApproval([ev("run.started")])).toBeNull();
  });
});

describe("time left", () => {
  it("is never negative and says Expired at zero", () => {
    expect(leftText(-30)).toBe("Expired");
    expect(leftText(0)).toBe("Expired");
    expect(leftText(0.4)).toBe("Expired");
    expect(leftText(null)).toBeNull();
    expect(leftText(45)).toBe("45 s left");
    expect(leftText(61)).toBe("1 min left");
    expect(leftText(12 * 60 + 5)).toBe("12 min left");
    expect(leftText(3600)).toBe("1 h left");
    expect(leftText(3600 + 5 * 60)).toBe("1 h 5 min left");
  });

  it("ages the server's figure by the time since it arrived, so a wrong local clock cannot change it", () => {
    expect(ageSeconds(100, 1_000, 31_000)).toBe(70);
    expect(ageSeconds(100, 1_000, 1_000_000)).toBe(0);
    expect(ageSeconds(100, 50_000, 1_000)).toBe(100); // a clock that went backwards
    expect(ageSeconds(null, 0, 10)).toBeNull();
  });
});

describe("marks and words", () => {
  it("states are glyph plus word; used approvals say so", () => {
    expect(approvalStateMeta({ state: "REQUESTED", consumed: false })).toMatchObject({
      word: "Waiting",
      tone: "wait",
    });
    expect(approvalStateMeta({ state: "APPROVED", consumed: true }).word).toBe("Approved, used");
    expect(approvalStateMeta({ state: "DENIED", consumed: false })).toMatchObject({
      word: "Denied",
      tone: "ink",
    });
    expect(approvalStateMeta({ state: "EXPIRED", consumed: false }).tone).toBe("wait");
  });
  it("risk classes, approver roles, tabs and dry-run decisions", () => {
    expect(riskMeta("R4")).toMatchObject({ word: "Risk R4", filled: true });
    expect(riskMeta("R3").tone).toBe("wait");
    expect(riskMeta(null).word).toBe("Unclassified");
    expect(approverText(null)).toBe("Owner or admin");
    expect(approverText("SECURITY")).toBe("Security or above");
    expect(parseApprovalTab("decided")).toBe("decided");
    expect(parseApprovalTab("nonsense")).toBe("pending");
    expect(dryDecisionMeta("require_approval").word).toBe("Would need approval");
  });
});

describe("the API's fixed errors, in words", () => {
  it("never echoes a message from the response", () => {
    expect(decisionErrorText("APPROVER_ROLE_REQUIRED", 403)).toBe(
      "Your role cannot decide this request.",
    );
    expect(decisionErrorText("SELF_APPROVAL_FORBIDDEN", 403)).toMatch(/your own API key/);
    expect(decisionErrorText("APPROVAL_NOT_REQUESTED", 409, "DENIED")).toBe(
      "Someone else decided first: it is now denied.",
    );
    expect(decisionErrorText("APPROVAL_NOT_REQUESTED", 409)).toBe("Someone else decided first.");
    expect(decisionErrorText("APPROVAL_NOT_FOUND", 404)).toBe("This approval was not found.");
    expect(decisionErrorText("SOMETHING_ELSE", 500)).toMatch(/Nothing was changed/);
  });
});

describe("editor helpers", () => {
  it("finds the caret offset of line:column and clamps", () => {
    const text = "one\ntwo\nthree";
    expect(offsetOf(text, 1, 1)).toBe(0);
    expect(offsetOf(text, 2, 2)).toBe(5);
    expect(offsetOf(text, 3, 99)).toBe(text.length);
    expect(offsetOf(text, 99, 1)).toBe(text.length);
  });
  it("classifies a unified diff; removed lines are their own kind (amber, not red)", () => {
    const kinds = diffLines("--- v1\n+++ v2\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n").map(
      (l) => l.kind,
    );
    expect(kinds).toEqual(["head", "head", "hunk", "ctx", "del", "add"]);
    expect(diffLines("")).toEqual([]);
  });
  it("the starter policy is valid for the fixtures' validator and fits the web proxy", () => {
    expect(fixtureValidate(STARTER_POLICY)).toMatchObject({ valid: true, rule_count: 3 });
    expect(tooLargeForWeb(STARTER_POLICY)).toBe(false);
    expect(tooLargeForWeb("x".repeat(70_000))).toBe(true);
  });
  it("the fixtures' validator reports the mistakes people make, with line and column", () => {
    const bad = fixtureValidate("version: 2\npolicies:\n  - id: Bad Id\n    decision: maybe\n");
    expect(bad.valid).toBe(false);
    expect(bad.issues.map((i) => [i.line, i.code])).toEqual([
      [1, "invalid_version"],
      [3, "invalid_rule_id"],
      [4, "invalid_value"],
    ]);
    expect(fixtureValidate("# nothing\n").issues[0]?.code).toBe("empty_document");
    expect(fixtureValidate("policies: []\n").issues[0]?.code).toBe("missing_key");
  });
});
