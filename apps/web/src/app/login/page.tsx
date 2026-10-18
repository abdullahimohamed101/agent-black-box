import Link from "next/link";
import { redirect } from "next/navigation";
import { auth, type LoginErrorKey } from "@/copy/auth";
import { safeReturnTo, fixturesMode } from "@/server/config";
import { loadMe } from "@/server/session";
import { Brand } from "@/components/shell/Brand";
import "../login.css";

export const dynamic = "force-dynamic";

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
  const known = params.error !== undefined && Object.hasOwn(auth.errors, params.error);
  const message = params.error
    ? auth.errors[known ? (params.error as LoginErrorKey) : "login_failed"]
    : null;
  const href = `/api/auth/login${returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : ""}`;
  return (
    <main id="main" className="auth-page">
      <Brand />
      <h1>{auth.loginTitle}</h1>
      {message && (
        <p role="alert" className="notice">
          {message}
        </p>
      )}
      {fixturesMode() ? (
        <p>
          {auth.fixtureNote} <Link href="/">{auth.fixtureContinue}</Link>
        </p>
      ) : (
        <>
          {/* A plain link, not next/link: prefetching it would start a sign-in. */}
          <a className="button" href={href}>
            {auth.signIn}
          </a>
          <p className="auth-aside">{auth.aside}</p>
        </>
      )}
    </main>
  );
}
