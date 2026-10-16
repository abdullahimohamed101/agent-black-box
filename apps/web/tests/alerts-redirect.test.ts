import { beforeEach, describe, expect, it, vi } from "vitest";

// Design pass 2 step 5c: Alerts moved under Settings. The old address keeps working: it redirects to the Alerts tab and keeps the
// query string, `?tab=` included, so a saved link or an email that points at /alerts still opens the same view.
const redirect = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ redirect }));
import AlertsRedirect from "../src/app/w/[workspace]/alerts/page";
import { alertsPath } from "../src/lib/routes";

const run = (search: Record<string, string | string[] | undefined>) =>
  AlertsRedirect({
    params: Promise.resolve({ workspace: "demo" }),
    searchParams: Promise.resolve(search),
  });
beforeEach(() => redirect.mockClear());

describe("/w/{ws}/alerts", () => {
  it("redirects to the Alerts tab of Settings", async () => {
    await run({});
    expect(redirect).toHaveBeenCalledWith("/w/demo/settings/alerts");
  });
  it.each(["rules", "destinations", "history"])("keeps ?tab=%s", async (tab) => {
    await run({ tab });
    expect(redirect).toHaveBeenCalledWith(`/w/demo/settings/alerts?tab=${tab}`);
  });
  it("keeps other parameters and repeated ones", async () => {
    await run({ tab: "history", status: ["failed", "sent"], gone: undefined });
    expect(redirect).toHaveBeenCalledWith(
      "/w/demo/settings/alerts?tab=history&status=failed&status=sent",
    );
  });
  it("the route helper points at the new address", () => {
    expect(alertsPath("demo")).toBe("/w/demo/settings/alerts");
    expect(alertsPath("demo", "history")).toBe("/w/demo/settings/alerts?tab=history");
    expect(alertsPath("demo", "rules")).toBe("/w/demo/settings/alerts");
  });
});
