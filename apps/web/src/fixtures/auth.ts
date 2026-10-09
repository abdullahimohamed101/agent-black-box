import type { components } from "@/lib/api/schema";
import { FIXTURE_PROJECT } from "./builder";

type Me = components["schemas"]["MeOut"];

/**
 * A fixture person for `ABB_WEB_DATA_SOURCE=fixtures` (gated like every fixture path). The permission lists are data
 * copied from the API's matrix so fixture screens look like the real ones; the app never derives them from the role.
 */
const OWNER_PERMISSIONS = [
  "analytics.read", "api_key.create", "api_key.read", "api_key.revoke", "artifact.read", "audit.read",
  "billing.read", "invite.read", "invite.write", "member.read", "member.write", "member.write_owner",
  "payload.read", "pricing.read", "pricing.write", "project.read", "project.write", "run.read",
  "workspace.read",
]; // prettier-ignore
const VIEWER_PERMISSIONS = [
  "analytics.read", "member.read", "pricing.read", "project.read", "run.read", "workspace.read",
]; // prettier-ignore

export const FIXTURE_USER = {
  id: "usr_00000000000000000000000001",
  email: "fixture@local.test",
  name: "Fixture User",
};
const WS_DEFAULT = {
  id: "ws_00000000000000000000000001",
  slug: "default",
  name: "Default workspace",
};
const WS_VIEWER = { id: "ws_00000000000000000000000002", slug: "viewer-only", name: "Viewer only" };

export const FIXTURE_ME: Me = {
  user: FIXTURE_USER,
  memberships: [
    { workspace: WS_DEFAULT, role: "OWNER", permissions: OWNER_PERMISSIONS, own_permissions: [] },
    { workspace: WS_VIEWER, role: "VIEWER", permissions: VIEWER_PERMISSIONS, own_permissions: [] },
  ],
};

export const FIXTURE_PROJECTS = [{ id: FIXTURE_PROJECT, slug: "demo", name: "Demo project" }];

const AT = "2026-10-07T09:00:00Z";
const FIXTURE_MEMBERS = [
  { user_id: FIXTURE_USER.id, email: FIXTURE_USER.email, name: FIXTURE_USER.name, role: "OWNER", joined_at: AT },
  { user_id: "usr_00000000000000000000000002", email: "dev@local.test", name: null, role: "DEVELOPER", joined_at: AT },
]; // prettier-ignore

/** Settings data served in fixture mode. Writes are refused by the proxy (fixtures are read-only). */
export function fixtureSettings(kind: string): unknown | null {
  switch (kind) {
    case "me":
      return FIXTURE_ME;
    case "projects":
      return { items: FIXTURE_PROJECTS };
    case "members":
      return { items: FIXTURE_MEMBERS };
    case "invitations":
      return { items: [] };
    case "api-keys":
      return { items: [] };
    case "pricing":
      return { prices: [] };
    case "audit":
      return { items: [], next_cursor: null };
    default:
      return null;
  }
}
