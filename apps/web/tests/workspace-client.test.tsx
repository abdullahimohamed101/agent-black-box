import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InviteAccept } from "@/components/InviteAccept";
import { WorkspaceProvider, useCan, useProjectId } from "@/components/WorkspaceProvider";
import {
  api,
  fieldErrors,
  setApiWorkspace,
  setSessionExpiredHandler,
  ApiRequestError,
} from "@/lib/api/client";
import { forgetInviteToken, takeInviteToken } from "@/lib/invite";
import type { WorkspaceContext } from "@/lib/workspace";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => "/",
}));

const ctx = (over: Partial<WorkspaceContext> = {}): WorkspaceContext => ({
  mode: "session",
  user: { id: "usr_1", email: "a@b.co", name: null },
  workspace: { id: "ws_00000000000000000000000001", slug: "acme", name: "Acme" },
  role: "VIEWER",
  permissions: ["run.read"],
  ownPermissions: [],
  memberships: [{ slug: "acme", name: "Acme", role: "VIEWER" }],
  projects: [{ id: "prj_1", slug: "web", name: "Web" }],
  ...over,
});

afterEach(() => {
  vi.unstubAllGlobals();
  setApiWorkspace(null);
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});

describe("workspace context", () => {
  function Probe() {
    return (
      <p>
        {String(useCan("run.read"))}|{String(useCan("member.write"))}|{String(useProjectId("web"))}|
        {String(useProjectId("all"))}|{String(useProjectId("prj_1"))}|{String(useProjectId("nope"))}
      </p>
    );
  }
  it("answers permission questions from the list it was given and resolves project slugs", () => {
    render(
      <WorkspaceProvider value={ctx()}>
        <Probe />
      </WorkspaceProvider>,
    );
    expect(screen.getByText("true|false|prj_1|null|prj_1|nope")).toBeInTheDocument();
  });

  it("sends the workspace header on API calls and stops when the page is gone", async () => {
    const seen: (string | null)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (r: Request) => {
        seen.push(r.headers.get("x-abb-workspace"));
        return Response.json({ items: [] });
      }),
    );
    const view = render(
      <WorkspaceProvider value={ctx()}>
        <p>x</p>
      </WorkspaceProvider>,
    );
    await api.GET("/v1/members");
    view.unmount();
    await api.GET("/v1/members");
    expect(seen).toEqual(["ws_00000000000000000000000001", null]);
  });
});

describe("401 handling", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  it("runs the sign-in redirect on SESSION_INVALID only", async () => {
    const handler = vi.fn();
    setSessionExpiredHandler(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "SESSION_INVALID" } }, { status: 401 })),
    );
    await api.GET("/v1/members");
    expect(handler).toHaveBeenCalledTimes(1);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "API_KEY_INVALID" } }, { status: 401 })),
    );
    await api.GET("/v1/members");
    expect(handler).toHaveBeenCalledTimes(1);
  });
  it("shows the API's per-field validation messages", () => {
    const err = new ApiRequestError("bad", 422, "REQUEST_INVALID", null, false, {
      errors: [
        { loc: ["body", "email"], msg: "bad email" },
        { loc: ["body", "role"], msg: "bad role" },
      ],
    });
    expect(fieldErrors(err)).toEqual({ email: "bad email", role: "bad role" });
    expect(fieldErrors(new Error("x"))).toEqual({});
  });
});

describe("invitation token handling (D12)", () => {
  const TOKEN = "T".repeat(43);
  it("reads the fragment once, clears the address bar and keeps it for this tab only", () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    expect(takeInviteToken()).toBe(TOKEN);
    expect(location.hash).toBe("");
    expect(takeInviteToken()).toBe(TOKEN); // survives the sign-in round trip
    forgetInviteToken();
    expect(takeInviteToken()).toBeNull();
  });
  it("never stores a fragment that does not look like a token, but still clears it", () => {
    history.replaceState(null, "", "/invite#<script>");
    expect(takeInviteToken()).toBeNull();
    expect(location.hash).toBe("");
  });

  it("asks a signed-out visitor to sign in, with a plain link back to /invite", async () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({}, { status: 401 })),
    );
    render(<InviteAccept />);
    const link = await screen.findByRole("link", { name: "Sign in" });
    expect(link).toHaveAttribute("href", "/api/auth/login?return_to=%2Finvite");
  });

  it("accepts on a click, posts the token in the body and opens the workspace", async () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = typeof input === "string" ? input : input.url;
        if (url.endsWith("/v1/me")) return Response.json({ user: { email: "me@x.co" } });
        calls.push({ url, body: await (input as Request).text() });
        return Response.json({
          workspace: { id: "ws_1", slug: "acme", name: "A" },
          role: "VIEWER",
        });
      }),
    );
    render(<InviteAccept />);
    await userEvent.click(await screen.findByRole("button", { name: "Accept invitation" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/w/acme/projects/all"));
    expect(calls[0]!.url).toContain("/api/abb/v1/invitations/accept");
    expect(JSON.parse(calls[0]!.body)).toEqual({ token: TOKEN });
    expect(takeInviteToken()).toBeNull();
  });

  it("shows the API's refusal as text and drops a link that can never work", async () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = typeof input === "string" ? input : input.url;
        if (url.endsWith("/v1/me")) return Response.json({ user: { email: "me@x.co" } });
        return Response.json(
          { error: { code: "INVITATION_EMAIL_MISMATCH", message: "<b>Wrong</b> email" } },
          { status: 403 },
        );
      }),
    );
    render(<InviteAccept />);
    await userEvent.click(await screen.findByRole("button", { name: "Accept invitation" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("<b>Wrong</b> email"); // text, not markup
    expect(alert.querySelector("b")).toBeNull();
    expect(takeInviteToken()).toBeNull();
  });
});
