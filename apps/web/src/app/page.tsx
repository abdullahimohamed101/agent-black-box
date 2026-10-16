import Link from "next/link";
import { redirect } from "next/navigation";
import { SignOutButton } from "@/components/SignOutButton";
import { ApiHealth } from "@/components/ApiHealth";
import { auth } from "@/copy/auth";
import { fixturesMode } from "@/server/config";
import { loadMe } from "@/server/session";
import { Brand } from "@/components/shell/Brand";
import "./login.css";

export const dynamic = "force-dynamic";

export default async function Home() {
  // Fixture data has no person: keep the plain entry page.
  if (fixturesMode()) {
    return (
      <main id="main" className="auth-page">
        <Brand />
        <h1>{auth.home.fixtureTitle}</h1>
        <p className="auth-context">{auth.home.fixtureLede}</p>
        <p>
          <Link href="/w/default/projects/all/runs">{auth.home.fixtureOpen}</Link>
        </p>
        <ApiHealth />
      </main>
    );
  }
  const me = await loadMe();
  if (me.status === "unauthenticated") redirect("/login");
  if (me.status === "unavailable") {
    return (
      <main id="main" className="auth-page">
        <Brand />
        <h1>{auth.home.fixtureTitle}</h1>
        <p role="alert" className="notice">
          {me.message} {auth.home.unavailableSuffix}
        </p>
      </main>
    );
  }
  const first = me.status === "ok" ? me.value.memberships[0] : undefined;
  if (first) redirect(`/w/${first.workspace.slug}/projects/all/runs`);
  return (
    <main id="main" className="auth-page">
      <Brand />
      <h1>{auth.home.noWorkspacesTitle}</h1>
      <p className="auth-context">{auth.home.noWorkspaces}</p>
      <SignOutButton />
    </main>
  );
}
