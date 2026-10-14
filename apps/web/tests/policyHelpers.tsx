import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { fixtureReply } from "@/fixtures/api";
import { fixturePolicyWrite } from "@/fixtures/policies";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import type { WorkspaceContext } from "@/lib/workspace";

export const PRJ = "prj_00000000000000000000000001";
export const apr = (n: number) => `apr_${String(n).padStart(26, "0")}`;
export const OWNER = [
  "run.read",
  "approval.read",
  "approval.decide",
  "policy.read",
  "policy.write",
  "audit.read",
];

export const ctx = (permissions: string[] = OWNER): WorkspaceContext => ({
  mode: "session",
  user: { id: "usr_1", email: "me@x.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "demo", name: "Demo" },
  role: "OWNER",
  permissions,
  ownPermissions: [],
  memberships: [],
  projects: [{ id: PRJ, slug: "demo", name: "Demo project" }],
});

export function mount(ui: React.ReactElement, permissions?: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

export type Call = { method: string; path: string; search: string; body: unknown };

/**
 * The fixture API behind `fetch`: reads through `fixtureReply`, the policy and approval writes through
 * `fixturePolicyWrite`. `over` can answer a request itself (to force an error or a particular body).
 */
export function stubPolicyApi(over?: (c: Call) => Response | undefined) {
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
          ? fixtureReply(segs, u.searchParams)
          : (fixturePolicyWrite(segs, call.body, { content: true }) ?? {
              status: 405,
              body: { error: { code: "METHOD_NOT_ALLOWED", message: "no", details: {} } },
            });
      return Response.json(r.body, { status: r.status });
    }),
  );
  return calls;
}

export const errorBody = (code: string, status: number, details: object = {}) =>
  Response.json(
    {
      error: {
        code,
        message: "API sentence that must not be shown",
        retryable: false,
        request_id: "r",
        details,
      },
    },
    { status },
  );
