import type { ProjectOut } from "@/lib/api/types";

/** Plain data about the page's workspace: built on the server, read by components (`useWorkspace`). */
export type WorkspaceContext = {
  /** `session`: a signed-in person. `key`/`fixtures`: the legacy shared key and fixture data (no person). */
  mode: "session" | "key" | "fixtures";
  user: { id: string; email: string; name: string | null } | null;
  /** `id` is null in key mode (the key's own workspace is implied and no header is sent). */
  workspace: { id: string | null; slug: string; name: string };
  role: string | null;
  permissions: string[];
  ownPermissions: string[];
  memberships: { slug: string; name: string; role: string }[];
  projects: ProjectOut[];
};
