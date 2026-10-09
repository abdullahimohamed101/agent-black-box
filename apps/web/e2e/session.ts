import type { BrowserContext, Page } from "@playwright/test";

/**
 * The E2E scripts mint a development session for a throwaway workspace owner (`create-session`, gated by
 * ABB_ENVIRONMENT and ABB_ALLOW_DEV_SESSIONS=1) and pass it as E2E_SESSION_TOKEN. The browser then holds exactly what a
 * signed-in person holds: the HttpOnly session cookie. There is no shared key anywhere.
 */
export async function signIn(context: BrowserContext, url: string): Promise<void> {
  const value = process.env.E2E_SESSION_TOKEN;
  if (!value)
    throw new Error("E2E_SESSION_TOKEN is not set (run the spec through its scripts/*-e2e.sh)");
  await context.addCookies([{ name: "abb_session", value, url, httpOnly: true, sameSite: "Lax" }]);
}

/** The slug of the workspace the script created; the page route resolves it through the API. */
export const workspace = (): string => process.env.E2E_WORKSPACE ?? "";

/** `X-ABB-Workspace` for direct calls through the proxy (the pages send it themselves): resolved from `/v1/me`. */
export async function workspaceHeader(page: Page): Promise<Record<string, string>> {
  const me = (await (await page.request.get("/api/abb/v1/me")).json()) as {
    memberships: { workspace: { id: string; slug: string } }[];
  };
  const found = me.memberships.find((m) => m.workspace.slug === workspace());
  if (!found) throw new Error(`the session is not a member of ${workspace()}`);
  return { "x-abb-workspace": found.workspace.id };
}
