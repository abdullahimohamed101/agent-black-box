import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auth } from "@/copy/auth";
import InvitePage from "@/app/invite/page";
import Home from "@/app/page";
import LoginPage from "@/app/login/page";

// Phase 22 step 10 (D11): sign-in and invitation are quiet pages inside the product. The auth logic is untouched: these
// tests pin the markup the redesign owns (one sentence, one action, the gutter class) and the behaviour it must keep.
const me = vi.hoisted(() => ({ value: { status: "unauthenticated" } as { status: string } }));
const fixtures = vi.hoisted(() => ({ on: false }));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/server/session", () => ({ loadMe: async () => me.value }));
vi.mock("@/server/config", async (orig) => ({
  ...(await orig<typeof import("@/server/config")>()),
  fixturesMode: () => fixtures.on,
}));

async function login(params: { return_to?: string; error?: string } = {}) {
  return render(await LoginPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  me.value = { status: "unauthenticated" };
  fixtures.on = false;
});
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});

describe("sign-in page", () => {
  it("is one sentence and one action, in the product's quiet page", async () => {
    const { container } = await login();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Sign in to see what your agents did",
    );
    const main = container.querySelector("main")!;
    expect(main).toHaveClass("auth-page");
    expect(main).toHaveAttribute("id", "main");
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link).toHaveAttribute("href", "/api/auth/login");
    // Exactly the brand link and the sign-in link are focusable: the keyboard path is two Tab stops.
    expect(main.querySelectorAll("a, button, input")).toHaveLength(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the return_to behaviour: a safe path rides along, an unsafe one is dropped", async () => {
    await login({ return_to: "/w/acme/projects/all" });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      `/api/auth/login?return_to=${encodeURIComponent("/w/acme/projects/all")}`,
    );
    document.body.innerHTML = "";
    await login({ return_to: "https://evil.example/" });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/api/auth/login",
    );
  });

  it("shows only fixed error strings, never the query value", async () => {
    await login({ error: "rate_limited" });
    expect(screen.getByRole("alert")).toHaveTextContent("Too many sign-in attempts");
    document.body.innerHTML = "";
    await login({ error: "<img src=x onerror=alert(1)>" });
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in did not complete");
    expect(document.body.innerHTML).not.toContain("onerror");
  });

  it("tells someone nobody has invited, in the same plain words, without naming any account (KI-076)", async () => {
    await login({ error: "no_access" });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "No access yet. Ask a workspace owner to invite you.",
    );
  });

  it("an already signed-in visitor is sent on, as before", async () => {
    me.value = { status: "ok" };
    await expect(login({ return_to: "/w/acme/projects/all" })).rejects.toThrow(
      "redirect:/w/acme/projects/all",
    );
  });

  it("fixture mode offers Continue instead of a sign-in", async () => {
    fixtures.on = true;
    await login();
    expect(screen.getByRole("link", { name: "Continue" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
  });
});

describe("invitation page", () => {
  const TOKEN = "T".repeat(43);

  it("is the same quiet page with one heading", () => {
    const { container } = render(<InvitePage />);
    expect(container.querySelector("main")).toHaveClass("auth-page");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Join a workspace");
  });

  it("names the invitation to a signed-out visitor and clears the token from the address bar", async () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({}, { status: 401 })),
    );
    render(<InvitePage />);
    await screen.findByText("An invitation to a workspace is waiting for you.");
    expect(
      screen.getByText(
        "Sign in with the email address the invitation was sent to, then accept it.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/api/auth/login?return_to=%2Finvite",
    );
    expect(location.hash).toBe("");
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("names the invitation and the signed-in address before accepting", async () => {
    history.replaceState(null, "", `/invite#${TOKEN}`);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ user: { email: "me@x.co" } })),
    );
    render(<InvitePage />);
    await screen.findByText("An invitation to a workspace is waiting for you.");
    expect(screen.getByText("me@x.co")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Accept invitation" })).toBeEnabled(),
    );
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("without an invitation link it says what the page needs and shows no invitation context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ user: { email: "me@x.co" } })),
    );
    render(<InvitePage />);
    await screen.findByText(/needs an invitation link/);
    expect(screen.queryByText("An invitation to a workspace is waiting for you.")).toBeNull();
  });
});

describe("the entry page and the pages outside the shell (design pass 2)", () => {
  it("a person with a workspace goes straight to its Runs (not the retired Overview)", async () => {
    me.value = {
      status: "ok",
      value: { memberships: [{ workspace: { slug: "acme" } }] },
    } as never;
    await expect(Home()).rejects.toThrow("redirect:/w/acme/projects/all/runs");
  });

  it("a signed-in person with no workspace sees one sentence and a sign-out, no workspace links", async () => {
    me.value = { status: "ok", value: { memberships: [] } } as never;
    render(await Home());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("No workspaces yet");
    expect(screen.getByText(auth.home.noWorkspaces)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(document.querySelector("main")).toHaveClass("auth-page");
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("fixture mode opens the runs, and says what the data is", async () => {
    fixtures.on = true;
    render(await Home());
    expect(screen.getByRole("link", { name: auth.home.fixtureOpen })).toHaveAttribute(
      "href",
      "/w/default/projects/all/runs",
    );
  });

  it("each of the six sign-in notices is the fixed sentence; an unknown key falls back to the generic one", async () => {
    for (const [key, sentence] of Object.entries(auth.errors)) {
      document.body.innerHTML = "";
      await login({ error: key });
      expect(screen.getByRole("alert")).toHaveTextContent(sentence);
    }
    document.body.innerHTML = "";
    await login({ error: "constructor" });
    expect(screen.getByRole("alert")).toHaveTextContent(auth.errors.login_failed);
  });
});
