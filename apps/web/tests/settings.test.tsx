import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiKeys } from "@/components/ApiKeys";
import { ArtifactText } from "@/components/ArtifactText";
import { AuditLog } from "@/components/AuditLog";
import { EventDrawer } from "@/components/EventDrawer";
import { Members } from "@/components/Members";
import { PricingOverrides } from "@/components/PricingOverrides";
import { SettingsNav } from "@/components/SettingsNav";
import { SettingsPage } from "@/components/SettingsPage";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { successRun } from "@/fixtures/runs";
import type { WorkspaceContext } from "@/lib/workspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/w/acme/settings/members",
}));

const ME = "usr_00000000000000000000000001";
const OTHER = "usr_00000000000000000000000002";
const OWNER = [
  "member.read", "member.write", "member.write_owner", "invite.read", "invite.write",
  "api_key.read", "api_key.create", "api_key.revoke", "pricing.read", "pricing.write", "audit.read",
]; // prettier-ignore

function ctx(permissions: string[], own: string[] = []): WorkspaceContext {
  return {
    mode: "session",
    user: { id: ME, email: "me@x.co", name: null },
    workspace: { id: "ws_00000000000000000000000001", slug: "acme", name: "Acme" },
    role: "X",
    permissions,
    ownPermissions: own,
    memberships: [{ slug: "acme", name: "Acme", role: "X" }],
    projects: [{ id: "prj_1", slug: "web", name: "Web" }],
  };
}
function mount(ui: React.ReactElement, c: WorkspaceContext) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={c}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}

type Handler = (req: { body: unknown; url: URL }) => Response;
/** Routes `METHOD /v1/path` to a handler; records every call. */
function api(routes: Record<string, Handler>) {
  const calls: { key: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      const url = new URL(r.url);
      const key = `${r.method} ${url.pathname.replace("/api/abb", "")}`;
      const text = r.method === "GET" ? "" : await r.text();
      const body = text ? JSON.parse(text) : null;
      calls.push({ key, body });
      const h = routes[key];
      return h
        ? h({ body, url })
        : Response.json({ error: { code: "NOPE", message: "no route" } }, { status: 404 });
    }),
  );
  return calls;
}
const err = (status: number, code: string, message: string, details = {}) =>
  Response.json(
    { error: { code, message, request_id: "req_7", retryable: false, details } },
    { status },
  );

afterEach(() => vi.unstubAllGlobals());

const MEMBERS = {
  items: [
    { user_id: ME, email: "me@x.co", name: "Me", role: "OWNER", joined_at: "2026-10-01T00:00:00Z" },
    {
      user_id: OTHER,
      email: "dev@x.co",
      name: null,
      role: "DEVELOPER",
      joined_at: "2026-10-02T00:00:00Z",
    },
  ],
};

describe("Members", () => {
  it("shows loading, then members with role and removal controls for someone allowed to write", async () => {
    api({
      "GET /v1/members": () => Response.json(MEMBERS),
      "GET /v1/invitations": () => Response.json({ items: [] }),
    });
    mount(<Members />, ctx(OWNER));
    expect(screen.getByText("Loading members…")).toBeInTheDocument();
    expect(await screen.findByLabelText("Role of dev@x.co")).toHaveValue("DEVELOPER");
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(2);
    expect(await screen.findByText("No open invitations")).toBeInTheDocument();
  });

  it("is read-only without member.write: no controls, no invitation section", async () => {
    const calls = api({ "GET /v1/members": () => Response.json(MEMBERS) });
    mount(<Members />, ctx(["member.read"]));
    expect(await screen.findByText("dev@x.co")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByText("Invitations")).toBeNull();
    expect(calls.map((c) => c.key)).toEqual(["GET /v1/members"]); // no invitations request without invite.read
  });

  it("without member.write_owner an OWNER row is locked and OWNER is not offered", async () => {
    api({
      "GET /v1/members": () => Response.json(MEMBERS),
      "GET /v1/invitations": () => Response.json({ items: [] }),
    });
    mount(<Members />, ctx(OWNER.filter((p) => p !== "member.write_owner")));
    await screen.findByLabelText("Role of dev@x.co");
    expect(screen.queryByLabelText("Role of me@x.co")).toBeNull();
    const options = within(screen.getByLabelText("Role of dev@x.co"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).not.toContain("OWNER");
  });

  it("changes a role, asks twice before removing, and shows a refusal as a banner", async () => {
    const calls = api({
      "GET /v1/members": () => Response.json(MEMBERS),
      "GET /v1/invitations": () => Response.json({ items: [] }),
      [`PATCH /v1/members/${OTHER}`]: () => Response.json({ ...MEMBERS.items[1], role: "VIEWER" }),
      [`DELETE /v1/members/${OTHER}`]: () =>
        err(409, "LAST_OWNER", "The last owner cannot be removed."),
    });
    mount(<Members />, ctx(OWNER));
    await userEvent.selectOptions(await screen.findByLabelText("Role of dev@x.co"), "VIEWER");
    await waitFor(() => expect(calls.some((c) => c.key.startsWith("PATCH"))).toBe(true));
    expect(calls.find((c) => c.key.startsWith("PATCH"))!.body).toEqual({ role: "VIEWER" });
    const row = screen.getByText("dev@x.co").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false); // first click only asks
    await userEvent.click(within(row).getByRole("button", { name: "Confirm remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The last owner cannot be removed.");
  });

  it("shows the invitation link once, as text, and clears it on Done", async () => {
    api({
      "GET /v1/members": () => Response.json(MEMBERS),
      "GET /v1/invitations": () => Response.json({ items: [] }),
      "POST /v1/invitations": ({ body }) =>
        Response.json(
          {
            invitation: {
              id: "inv_1",
              email: (body as { email: string }).email,
              role: "VIEWER",
              invited_by: ME,
              created_at: "2026-10-09T00:00:00Z",
              expires_at: "2026-10-16T00:00:00Z",
            },
            link: "http://localhost:3000/invite#<b>TOKEN</b>",
          },
          { status: 201 },
        ),
    });
    mount(<Members />, ctx(OWNER));
    await userEvent.type(await screen.findByLabelText("Email"), "new@x.co");
    await userEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    const link = await screen.findByTestId("invite-link");
    expect(link).toHaveTextContent("http://localhost:3000/invite#<b>TOKEN</b>");
    expect(link.querySelector("b")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("invite-link")).toBeNull();
  });

  it("shows API validation per field", async () => {
    api({
      "GET /v1/members": () => Response.json(MEMBERS),
      "GET /v1/invitations": () => Response.json({ items: [] }),
      "POST /v1/invitations": () =>
        err(422, "REQUEST_INVALID", "Request validation failed.", {
          errors: [{ loc: ["body", "email"], msg: "String should match pattern" }],
        }),
    });
    mount(<Members />, ctx(OWNER));
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.co");
    await userEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    expect(await screen.findByText("String should match pattern")).toBeInTheDocument();
  });

  it("shows an error state with retry when members cannot load", async () => {
    api({ "GET /v1/members": () => err(503, "DEPENDENCY_UNAVAILABLE", "Database unavailable.") });
    mount(<Members />, ctx(["member.read"]));
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("ApiKeys", () => {
  const KEY = (id: string, by: string | null) => ({
    key_id: id, name: `key ${id}`, scopes: ["runs:read"], project_id: null, created_by: by,
    created_at: "2026-10-01T00:00:00Z", last_used_at: null, expires_at: null, status: "active",
  }); // prettier-ignore

  it("shows the token once after creation and sends the chosen scopes and project", async () => {
    const calls = api({
      "GET /v1/api-keys": () => Response.json({ items: [] }),
      "POST /v1/api-keys": () =>
        Response.json(
          { key: KEY("kid_new", ME), token: "abb_live_kid_new.SECRET" },
          { status: 201 },
        ),
    });
    mount(<ApiKeys />, ctx(OWNER));
    expect(await screen.findByText("No API keys")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "ci");
    await userEvent.click(screen.getByLabelText("runs:read"));
    await userEvent.selectOptions(screen.getByLabelText("Project"), "prj_1");
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByTestId("key-token")).toHaveTextContent("abb_live_kid_new.SECRET");
    expect(calls.find((c) => c.key === "POST /v1/api-keys")!.body).toEqual({
      name: "ci", scopes: ["events:write", "runs:read"], project_id: "prj_1", expires_in_days: null,
    }); // prettier-ignore
    await userEvent.click(screen.getByRole("button", { name: "I have stored it" }));
    expect(screen.queryByTestId("key-token")).toBeNull();
    expect(document.body.textContent).not.toContain("SECRET");
  });

  it("offers Revoke on every key to a role that revokes any, only on own keys otherwise, and none to viewers", async () => {
    const list = () => Response.json({ items: [KEY("a", ME), KEY("b", OTHER), KEY("c", null)] });
    api({ "GET /v1/api-keys": list });
    const { unmount } = mount(<ApiKeys />, ctx(OWNER));
    await screen.findByText("key a");
    expect(screen.getAllByRole("button", { name: /Revoke key/ })).toHaveLength(3);
    unmount();
    mount(<ApiKeys />, ctx(["api_key.read", "api_key.create"], ["api_key.revoke"]));
    await screen.findByText("key a");
    expect(screen.getAllByRole("button", { name: /Revoke key/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Revoke key key a" })).toBeInTheDocument();
  });

  it("hides creation without api_key.create", async () => {
    api({ "GET /v1/api-keys": () => Response.json({ items: [] }) });
    mount(<ApiKeys />, ctx(["api_key.read"]));
    await screen.findByText("No API keys");
    expect(screen.queryByRole("button", { name: "Create key" })).toBeNull();
  });

  it("revokes and refreshes; a 403 is a banner, not a crash", async () => {
    let revoked = false;
    api({
      "GET /v1/api-keys": () => Response.json({ items: revoked ? [] : [KEY("a", ME)] }),
      "DELETE /v1/api-keys/a": () => {
        revoked = true;
        return new Response(null, { status: 204 });
      },
    });
    mount(<ApiKeys />, ctx(OWNER));
    await userEvent.click(await screen.findByRole("button", { name: /Revoke key/ }));
    expect(await screen.findByText("No API keys")).toBeInTheDocument();
  });

  it("explains a refusal", async () => {
    api({
      "GET /v1/api-keys": () => Response.json({ items: [KEY("a", ME)] }),
      "DELETE /v1/api-keys/a": () => err(403, "PERMISSION_DENIED", "Not allowed."),
    });
    mount(<ApiKeys />, ctx(OWNER));
    await userEvent.click(await screen.findByRole("button", { name: /Revoke key/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your role does not allow this.");
  });
});

describe("PricingOverrides", () => {
  const PRICE = {
    pricing_version: "v1", origin: "builtin", provider: null, model_pattern: "gpt-*",
    valid_from: "2026-01-01T00:00:00Z", valid_to: null, input_per_million: "2.5",
    output_per_million: "10", cached_input_per_million: null, request_price: "0", currency: "USD",
    source: "x", project_id: null,
  }; // prettier-ignore

  it("lists prices; a reader sees no forms", async () => {
    api({ "GET /v1/pricing": () => Response.json({ prices: [PRICE] }) });
    mount(<PricingOverrides />, ctx(["pricing.read"]));
    expect(await screen.findByText("gpt-*")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add override" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rebuild costs" })).toBeNull();
  });

  it("submits an override, shows 422 per field, then rebuilds with counts", async () => {
    let first = true;
    const calls = api({
      "GET /v1/pricing": () => Response.json({ prices: [] }),
      "POST /v1/pricing/overrides": () => {
        if (first) {
          first = false;
          return err(422, "REQUEST_INVALID", "bad", {
            errors: [{ loc: ["body", "input_per_million"], msg: "Too large" }],
          });
        }
        return Response.json(PRICE, { status: 201 });
      },
      "POST /v1/cost/rebuild": () =>
        Response.json({ matched: 3, queued: 2, truncated: false }, { status: 202 }),
    });
    mount(<PricingOverrides />, ctx(OWNER));
    await userEvent.type(await screen.findByLabelText("Model pattern"), "my-model*");
    await userEvent.type(screen.getByLabelText("Input per million"), "9999999");
    await userEvent.type(screen.getByLabelText("Output per million"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findByText("Too large")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findByText("Override added.")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "POST /v1/pricing/overrides")[1]!.body).toMatchObject({
      model_pattern: "my-model*", input_per_million: "9999999", output_per_million: "1", project_id: null,
    }); // prettier-ignore
    await userEvent.click(screen.getByRole("button", { name: "Rebuild costs" }));
    expect(await screen.findByText(/Matched 3 runs, queued 2/)).toBeInTheDocument();
  });
});

describe("AuditLog", () => {
  const ROW = (id: string) => ({
    id, occurred_at: "2026-10-09T10:00:00Z", actor_kind: "user", actor_id: `user:${ME}`,
    action: "api_key.create", outcome: "allowed", resource_kind: "api_key", resource_id: "kid", details: { name: "<i>x</i>" }, request_id: null,
  }); // prettier-ignore

  it("pages newest first and renders details as text", async () => {
    api({
      "GET /v1/audit": ({ url }) =>
        url.searchParams.get("cursor")
          ? Response.json({ items: [ROW("2")], next_cursor: null })
          : Response.json({ items: [ROW("1")], next_cursor: "c1" }),
    });
    mount(<AuditLog />, ctx(OWNER));
    expect(await screen.findAllByText("api_key.create")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByText("api_key.create")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    const pre = document.querySelector("pre")!;
    expect(pre.textContent).toContain("<i>x</i>");
    expect(pre.querySelector("i")).toBeNull();
  });

  it("has empty and error states", async () => {
    api({ "GET /v1/audit": () => Response.json({ items: [], next_cursor: null }) });
    const { unmount } = mount(<AuditLog />, ctx(OWNER));
    expect(await screen.findByText("Nothing recorded yet")).toBeInTheDocument();
    unmount();
    api({ "GET /v1/audit": () => err(500, "INTERNAL", "Boom.") });
    mount(<AuditLog />, ctx(OWNER));
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom.");
  });
});

describe("permission-driven navigation", () => {
  it("shows only the tabs the API's permission list allows", () => {
    mount(<SettingsNav />, ctx(["member.read", "pricing.read"]));
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Members", "Pricing"]);
  });
  it("replaces a page the person cannot use with a clear note, and fetches nothing", () => {
    const calls = api({});
    mount(
      <SettingsPage needs="audit.read" what="the audit log">
        <AuditLog />
      </SettingsPage>,
      ctx(["member.read"]),
    );
    expect(screen.getByText("Not available for your role")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });
});

describe("withheld content (VIEWER)", () => {
  it("says the event payload is hidden by role instead of showing an empty or broken panel", async () => {
    const event = successRun().events.find((e) => e.has_payload)!;
    api({
      [`GET /v1/runs/${event.run_id}/events/${event.event_id}`]: () =>
        Response.json({ ...event, payload: null, payload_withheld: true }),
    });
    mount(<EventDrawer runId={event.run_id} event={event} onClose={() => {}} />, ctx(["run.read"]));
    expect(await screen.findByTestId("payload-withheld")).toHaveTextContent(
      "Content hidden by your role",
    );
  });

  it("says artifact content is hidden by role on a 403", async () => {
    api({ "GET /v1/artifacts/art_X/content": () => err(403, "PERMISSION_DENIED", "Denied.") });
    mount(<ArtifactText id="art_X" label="stdout" defaultOpen />, ctx(["run.read"]));
    expect(await screen.findByTestId("content-withheld")).toHaveTextContent(
      "Content hidden by your role",
    );
  });
});
