import type { ProjectOut } from "@/lib/api/types";

/** Plain data about the page's workspace: built on the server, read by components (`useWorkspace`). */
export type WorkspaceContext = {
  /** `session`: a signed-in person. `fixtures`: fixture data (a made-up person, no API). */
  mode: "session" | "fixtures";
  user: { id: string; email: string; name: string | null } | null;
  workspace: { id: string; slug: string; name: string };
  role: string | null;
  permissions: string[];
  ownPermissions: string[];
  memberships: { slug: string; name: string; role: string }[];
  projects: ProjectOut[];
};
