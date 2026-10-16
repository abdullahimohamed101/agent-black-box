import { describe, expect, it } from "vitest";
import { ApiRequestError } from "@/lib/api/client";
import {
  changedBody,
  draftsOf,
  formatBytes,
  parseDraft,
  pendingSentence,
  cancelBody,
  graceSentence,
  previewTiming,
  previewParams,
  previewSentence,
  previewSummary,
  propose,
  reportCounts,
  retentionError,
  shortened,
  type PreviewTier,
  type TierPolicy,
  type RetentionPolicy,
  type RetentionReport,
} from "@/lib/retention";

const policy = (over: Partial<Record<string, [number | null, "default" | "workspace"]>> = {}) =>
  ({
    mode: "dry_run",
    floor_days: 7,
    audit_floor_days: 90,
    grace_hours: 24,
    updated_at: null,
    updated_by: null,
    tiers: (
      [
        ["artifacts", 30],
        ["trace", 90],
        ["run", 365],
        ["audit", 365],
      ] as const
    ).map(([tier, def]) => {
      const [days, source] = over[tier] ?? [def, "default"];
      return { tier, days, forever: days === null, source, default_days: def };
    }),
  }) as RetentionPolicy;

describe("the audit log's own minimum and the grace period", () => {
  it("applies the audit minimum to the audit tier only", () => {
    const p = policy();
    const d = draftsOf(p);
    d.audit = { choice: "days", days: "60" };
    expect(propose(p, d)).toEqual({
      ok: false,
      errors: { audit: "Keep data for at least 90 days." },
    });
    d.audit = { choice: "days", days: "90" };
    d.artifacts = { choice: "days", days: "7" };
    expect(propose(p, d).ok).toBe(true);
    expect(changedBody(p, d)).toEqual({ artifacts: 7, audit: 90 });
  });
  const waiting: TierPolicy = {
    tier: "trace",
    days: 60,
    forever: false,
    source: "workspace",
    default_days: 90,
    pending: { effective_at: "2026-10-16T09:30:00Z", current_days: 90, current_forever: false },
  };
  it("says when a saved shortening takes effect and which period still applies", () => {
    expect(pendingSentence(waiting)).toBe(
      "This shortening takes effect on 2026-10-16 09:30 UTC; until then the longer period (90 days) still applies.",
    );
    expect(pendingSentence({ ...waiting, pending: null })).toBeNull();
  });
  it("cancels by saving the period in force (a lengthening, which applies at once)", () => {
    expect(cancelBody(waiting)).toEqual({ trace: 90 });
    const forever: TierPolicy = {
      ...waiting,
      pending: { effective_at: "2026-10-16T09:30:00Z", current_days: null, current_forever: true },
    };
    expect(cancelBody(forever)).toEqual({ trace: "forever" });
    expect(cancelBody({ ...waiting, pending: null })).toBeNull();
  });
  it("words the waiting time, or its absence", () => {
    expect(graceSentence(policy())).toContain("24 hours after you save");
    expect(graceSentence({ ...policy(), grace_hours: 0 })).toBe(
      "A shorter period applies as soon as you save.",
    );
  });
  it("shows both periods in a preview", () => {
    expect(previewTiming(tier({ effective_at: "2026-10-16T09:30:00Z", enforced_days: 90 }))).toBe(
      "Takes effect on 2026-10-16 09:30 UTC; until then 90 days still applies.",
    );
    expect(previewTiming(tier({}))).toBe("");
  });
});

describe("parseDraft", () => {
  it("accepts the default, forever and whole days from the floor", () => {
    expect(parseDraft({ choice: "default", days: "" }, 7)).toEqual({ ok: true, setting: null });
    expect(parseDraft({ choice: "forever", days: "" }, 7)).toEqual({
      ok: true,
      setting: "forever",
    });
    expect(parseDraft({ choice: "days", days: " 7 " }, 7)).toEqual({ ok: true, setting: 7 });
  });
  it("refuses below the floor, above the ceiling and anything that is not a whole number, without echoing it", () => {
    for (const days of ["6", "0", "36501", "", "abc", "1.5", "-3", "1e3", "９"]) {
      const r = parseDraft({ choice: "days", days }, 7);
      expect(r.ok, days).toBe(false);
      if (!r.ok) expect(r.message).not.toContain(days === "" ? "\u0000" : days);
    }
  });
});

describe("propose", () => {
  it("resolves the default to the installation's days and forever to null", () => {
    const p = policy();
    const d = draftsOf(p);
    d.trace = { choice: "forever", days: "" };
    d.run = { choice: "forever", days: "" };
    const r = propose(p, d);
    expect(r.ok && r.effective).toEqual({ artifacts: 30, trace: null, run: null, audit: 365 });
  });
  it("enforces the order the API enforces: artifacts <= trace <= run record", () => {
    const p = policy();
    const d = draftsOf(p);
    d.artifacts = { choice: "days", days: "120" };
    const r = propose(p, d);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.artifacts).toMatch(/cannot be kept longer than trace detail/);
    d.artifacts = { choice: "forever", days: "" };
    const forever = propose(p, d);
    expect(forever.ok).toBe(false);
  });
});

describe("changedBody and shortened", () => {
  it("sends only the tiers that differ from what is stored, null for the default", () => {
    const p = policy({ trace: [60, "workspace"] });
    const d = draftsOf(p);
    expect(changedBody(p, d)).toEqual({});
    d.trace = { choice: "default", days: "" };
    d.audit = { choice: "forever", days: "" };
    d.artifacts = { choice: "days", days: "14" };
    expect(changedBody(p, d)).toEqual({ trace: null, audit: "forever", artifacts: 14 });
  });
  it("names the tiers whose period gets shorter, and not those made longer or unchanged", () => {
    const p = policy();
    const d = draftsOf(p);
    d.artifacts = { choice: "days", days: "14" }; // 30 -> 14: shorter
    d.trace = { choice: "days", days: "120" }; // 90 -> 120: longer
    d.audit = { choice: "forever", days: "" }; // longer
    const r = propose(p, d);
    expect(r.ok && shortened(p, r.effective)).toEqual(["artifacts"]);
    const f = policy({ run: [null, "workspace"] });
    const g = draftsOf(f);
    g.run = { choice: "days", days: "400" }; // forever -> 400: shorter
    const r2 = propose(f, g);
    expect(r2.ok && shortened(f, r2.effective)).toEqual(["run"]);
  });
  it("sends the preview every tier with its effective value", () => {
    expect(previewParams({ artifacts: 14, trace: null, run: null, audit: 365 })).toEqual({
      artifacts_days: "14",
      trace_days: "forever",
      run_days: "forever",
      audit_days: "365",
    });
  });
});

const tier = (over: Partial<PreviewTier>): PreviewTier => ({
  tier: "trace",
  days: 30,
  forever: false,
  enforced_days: 30,
  cutoff: null,
  runs: 12,
  events: 340,
  artifact_bytes: 1.5 * 1024 ** 2,
  rows: 60,
  truncated: false,
  ...over,
});

describe("sentences", () => {
  it("says what a tier would remove, and that a truncated count is a lower bound", () => {
    expect(previewSentence(tier({}))).toBe(
      "This would remove 12 runs, 340 events and 1.5 MiB of artifacts.",
    );
    expect(previewSentence(tier({ runs: 1, events: 1, truncated: true }))).toContain(
      "at least 1 run",
    );
    expect(previewSentence(tier({ forever: true }))).toBe(
      "Kept forever: nothing would be removed.",
    );
    expect(previewSentence(tier({ tier: "audit", rows: 1 }))).toBe(
      "This would remove 1 audit entry.",
    );
  });
  it("summarises tiers by the largest figure, since tiers overlap", () => {
    expect(
      previewSummary([
        tier({ runs: 5, events: 60 }),
        tier({ tier: "artifacts", runs: 9, events: 0 }),
      ]),
    ).toBe("This would remove 9 runs, 60 events and 1.5 MiB of artifacts.");
  });
  it("formats bytes in binary units", () => {
    expect([0, 1023, 1024, 1536, 5 * 1024 ** 3].map(formatBytes)).toEqual([
      "0 B",
      "1023 B",
      "1 KiB",
      "1.5 KiB",
      "5 GiB",
    ]);
  });
  it("words a report by its mode: would remove (dry run) or removed (enforced)", () => {
    const r = {
      id: 1, tier: "trace", mode: "dry_run", runs: 2, events: 24, artifact_bytes: 0, rows: 1,
      truncated: false, cutoff_at: null, started_at: "", finished_at: "",
    } as RetentionReport; // prettier-ignore
    expect(reportCounts(r)).toContain("would remove 2 runs, 24 events");
    expect(reportCounts({ ...r, mode: "enforce" })).toContain("removed 2 runs");
  });
});

describe("retentionError", () => {
  const err = (status: number, code: string, retryAfter: number | null = null, message = "m") =>
    new ApiRequestError(message, status, code, null, false, {}, retryAfter);
  it("maps the rate limit with its Retry-After, the preview timeout, 404 and 403", () => {
    expect(retentionError(err(429, "RATE_LIMITED", 12))).toEqual({
      text: "Too many requests for this workspace. Try again in 12 seconds.",
      retry: true,
    });
    expect(retentionError(err(429, "RATE_LIMITED")).text).toContain("Try again shortly");
    expect(retentionError(err(504, "RETENTION_PREVIEW_TIMEOUT"))).toMatchObject({ retry: true });
    expect(retentionError(err(404, "NOT_FOUND"))).toMatchObject({ retry: false });
    expect(retentionError(err(403, "PERMISSION_DENIED")).text).toMatch(/role/);
    expect(
      retentionError(err(422, "RETENTION_POLICY_INVALID", null, "At least 7 days.")).text,
    ).toBe("At least 7 days.");
    expect(retentionError(new Error("boom")).text).toBe("The request failed.");
  });
});
