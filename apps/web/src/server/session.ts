import { cache } from "react";
import { cookies } from "next/headers";
import { FIXTURE_ME, FIXTURE_PROJECTS } from "@/fixtures/auth";
import type { components } from "@/lib/api/schema";
import type { WorkspaceContext } from "@/lib/workspace";
import {
  UPSTREAM_TIMEOUT_MS,
  apiBase,
  fixturesBlocked,
  fixturesMode,
  sessionCookieFor,
  sessionCookieName,
} from "./config";

/**
 * Who the visitor is and which workspace the page is for, resolved on the server with the visitor's own cookie.
 * `GET /v1/me` is the authority: a missing, expired or revoked session is a 401 there (D13). The result is handed to
 * the browser as plain data (`WorkspaceContext`); components show or hide by `permissions` and never derive them.
 */

type Me = components["schemas"]["MeOut"];
type Project = components["schemas"]["ProjectOut"];

export type Loaded<T> =
  | { status: "ok"; value: T }
  | { status: "unauthenticated" }
  | { status: "not_found" }
  | { status: "unavailable"; message: string };

async function getJson<T>(path: string, headers: Record<string, string>): Promise<Loaded<T>> {
  try {
    const res = await fetch(`${apiBase()}${path}`, {
      headers: { accept: "application/json", ...headers },
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (res.status === 401) return { status: "unauthenticated" };
    if (res.status === 404) return { status: "not_found" };
    if (!res.ok) return { status: "unavailable", message: `The API answered ${res.status}.` };
    return { status: "ok", value: (await res.json()) as T };
  } catch {
    return { status: "unavailable", message: "The API could not be reached." };
  }
}

/** `/v1/me` for a cookie header (null when there is no session cookie). Fixtures answer with the fixture person. */
export async function fetchMe(cookieHeader: string | null): Promise<Loaded<Me>> {
  if (fixturesMode()) {
    if (fixturesBlocked()) return { status: "unavailable", message: "Fixture data is disabled." };
    return { status: "ok", value: FIXTURE_ME };
  }
  const session = sessionCookieFor(cookieHeader);
  if (!session) return { status: "unauthenticated" };
  return getJson<Me>("/v1/me", { cookie: `${sessionCookieName()}=${session}` });
}

export async function resolveWorkspace(
  slug: string,
  cookieHeader: string | null,
): Promise<Loaded<WorkspaceContext>> {
  if (fixturesMode()) {
    const me = await fetchMe(cookieHeader);
    if (me.status !== "ok") return me;
    const found = me.value.memberships.find((m) => m.workspace.slug === slug);
    // Fixture mode accepts any slug (the e2e specs use their own) as an owner of an unlisted workspace.
    const m = found ?? {
      workspace: { id: "ws_00000000000000000000000009", slug, name: slug },
      role: "OWNER",
      permissions: FIXTURE_ME.memberships[0]!.permissions,
      own_permissions: [],
    };
    return {
      status: "ok",
      value: toContext("fixtures", me.value, m, FIXTURE_PROJECTS),
    };
  }
  const session = sessionCookieFor(cookieHeader);
  if (!session) return { status: "unauthenticated" };
  const me = await fetchMe(cookieHeader);
  if (me.status !== "ok") return me;
  const membership = me.value.memberships.find((m) => m.workspace.slug === slug);
  if (!membership) return { status: "not_found" }; // not a member looks the same as not existing (D7)
  const projects = await getJson<{ items: Project[] }>("/v1/projects", {
    cookie: `${sessionCookieName()}=${session}`,
    "x-abb-workspace": membership.workspace.id,
  });
  if (projects.status !== "ok") return projects;
  return { status: "ok", value: toContext("session", me.value, membership, projects.value.items) };
}

function toContext(
  mode: "session" | "fixtures",
  me: Me,
  m: Me["memberships"][number],
  projects: Project[],
): WorkspaceContext {
  return {
    mode,
    user: { id: me.user.id, email: me.user.email, name: me.user.name },
    workspace: { id: m.workspace.id, slug: m.workspace.slug, name: m.workspace.name },
    role: m.role,
    permissions: m.permissions,
    ownPermissions: m.own_permissions,
    memberships: me.memberships.map((x) => ({
      slug: x.workspace.slug,
      name: x.workspace.name,
      role: x.role,
    })),
    projects,
  };
}

/** The request's own `Cookie` header, for server components. */
export async function requestCookieHeader(): Promise<string | null> {
  const jar = await cookies();
  const all = jar.getAll();
  return all.length ? all.map((c) => `${c.name}=${c.value}`).join("; ") : null;
}

/** Deduplicated per request: the workspace layout and the pages under it share one lookup. */
export const loadWorkspace = cache(async (slug: string): Promise<Loaded<WorkspaceContext>> => {
  return resolveWorkspace(slug, await requestCookieHeader());
});

export const loadMe = cache(async (): Promise<Loaded<Me>> => fetchMe(await requestCookieHeader()));
