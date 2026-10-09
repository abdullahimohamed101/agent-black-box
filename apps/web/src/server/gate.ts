import { cookieValue, safeReturnTo, sessionCookieName, sessionOnly } from "./config";

/**
 * The cheap first gate for workspace pages (D13): without a session cookie there is nothing to authenticate, so go to
 * /login at once. It trusts nothing: a cookie that is present only gets the visitor as far as the layout, which asks
 * the API. In fixture mode and while the legacy key fallback exists there is no session to require.
 */
export function loginRedirect(
  pathname: string,
  search: string,
  cookieHeader: string | null,
): string | null {
  if (!sessionOnly()) return null;
  if (cookieValue(cookieHeader, sessionCookieName())) return null;
  const back = safeReturnTo(`${pathname}${search}`) ?? safeReturnTo(pathname);
  return back ? `/login?return_to=${encodeURIComponent(back)}` : "/login";
}
