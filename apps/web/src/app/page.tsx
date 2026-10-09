import Link from "next/link";
import { redirect } from "next/navigation";
import { SignOutButton } from "@/components/SignOutButton";
import { ApiHealth } from "@/components/ApiHealth";
import { fixturesMode, legacyKey } from "@/server/config";
import { loadMe } from "@/server/session";

export const dynamic = "force-dynamic";

export default async function Home() {
  // Fixture data and the legacy shared key have no person: keep the plain entry page.
  if (fixturesMode() || legacyKey()) {
    return (
      <main>
        <h1>Agent Black Box</h1>
        <p className="muted">Flight recorder for AI agents. </p>
        <p>
          <Link href="/w/default/projects/all">Open the dashboard →</Link>
        </p>
        <ApiHealth />
      </main>
    );
  }
  const me = await loadMe();
  if (me.status === "unauthenticated") redirect("/login");
  if (me.status === "unavailable") {
    return (
      <main>
        <h1>Agent Black Box</h1>
        <p role="alert" className="notice">
          {me.message} Try again shortly.
        </p>
      </main>
    );
  }
  const first = me.status === "ok" ? me.value.memberships[0] : undefined;
  if (first) redirect(`/w/${first.workspace.slug}/projects/all`);
  return (
    <main>
      <h1>No workspaces yet</h1>
      <p className="muted">
        You are signed in, but you are not a member of any workspace. Ask a workspace owner for an
        invitation link, then open it.
      </p>
      <SignOutButton />
    </main>
  );
}
