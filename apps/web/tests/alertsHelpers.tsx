import { vi } from "vitest";
import { fixtureReply } from "@/fixtures/api";
import { fixtureAlertWrite } from "@/fixtures/alerts";
import { type Call } from "./policyHelpers";

export const MANAGE = ["run.read", "alert.read", "alert.write", "alert.destination.read"];
export const READER = ["run.read", "alert.read"];

/** The fixture API behind `fetch` for the Alerts screens; `over` can answer a request itself to force an outcome. */
export function stubAlertApi(manage: boolean, over?: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const req = typeof input === "string" ? null : input;
      const u = new URL(req ? req.url : String(input));
      const method = (req?.method ?? init?.method ?? "GET").toUpperCase();
      const text = req && method !== "GET" ? await req.clone().text() : "";
      const call: Call = {
        method,
        path: u.pathname.replace(/^\/api\/abb\//, ""),
        search: u.search,
        body: text ? JSON.parse(text) : null,
      };
      calls.push(call);
      const o = over?.(call);
      if (o) return o;
      const segs = call.path.split("/");
      const r =
        method === "GET"
          ? fixtureReply(segs, u.searchParams, { content: manage })
          : (fixtureAlertWrite(method, segs, call.body, { manage }) ?? {
              status: 405,
              body: { error: { code: "METHOD_NOT_ALLOWED", message: "no", details: {} } },
            });
      return r.status === 204
        ? new Response(null, { status: 204 })
        : Response.json(r.body, { status: r.status });
    }),
  );
  return calls;
}
