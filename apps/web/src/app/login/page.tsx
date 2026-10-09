import Link from "next/link";
import { redirect } from "next/navigation";
import { safeReturnTo, fixturesMode } from "@/server/config";
import { loadMe } from "@/server/session";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  login_failed: "Sign-in did not complete. Try again.",
  rate_limited: "Too many sign-in attempts. Wait a minute and try again.",
  unavailable: "The sign-in service is unavailable. Try again shortly.",
  not_configured: "Sign-in is not configured on this server.",
  logout_failed: "Signing out failed; your session may still be active.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ return_to?: string; error?: string }>;
}) {
  const params = await searchParams;
  const returnTo = safeReturnTo(params.return_to);
  const me = await loadMe();
  // A visitor who is already signed in has nothing to do here (a stale cookie yields 401 and shows the form).
  if (me.status === "ok" && !fixturesMode()) redirect(returnTo ?? "/");
  // Only fixed strings are shown, never the query value (it is attacker-controllable text).
  const message = params.error ? (MESSAGES[params.error] ?? MESSAGES.login_failed) : null;
  const href = `/api/auth/login${returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : ""}`;
  return (
    <main>
      <h1>Sign in to Agent Black Box</h1>
      {message && (
        <p role="alert" className="notice">
          {message}
        </p>
      )}
      {fixturesMode() ? (
        <p>
          Fixture mode: no sign-in is needed. <Link href="/">Continue</Link>
        </p>
      ) : (
        <>
          <p className="muted">You sign in with your organization&apos;s identity provider.</p>
          {/* A plain link, not next/link: prefetching it would start a sign-in. */}
          <p>
            <a className="button" href={href}>
              Sign in
            </a>
          </p>
        </>
      )}
    </main>
  );
}
