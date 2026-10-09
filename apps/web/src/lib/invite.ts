/**
 * The invitation token travels in the URL fragment (`/invite#<token>`), so it never reaches a server or proxy log
 * (D12). It is read once, removed from the address bar, and kept in sessionStorage (this tab only) so that signing in
 * in between does not lose it. The shape matches the API's `AcceptIn`.
 */
export const INVITE_TOKEN = /^[A-Za-z0-9_-]{20,128}$/;
const KEY = "abb.invite";

export function takeInviteToken(): string | null {
  try {
    const fromUrl = location.hash.slice(1);
    if (fromUrl) {
      if (INVITE_TOKEN.test(fromUrl)) sessionStorage.setItem(KEY, fromUrl);
      history.replaceState(null, "", location.pathname + location.search);
    }
    const kept = sessionStorage.getItem(KEY);
    return kept && INVITE_TOKEN.test(kept) ? kept : null;
  } catch {
    return null; // storage can be blocked; the fragment was still cleared above when possible
  }
}

export function forgetInviteToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* nothing to forget */
  }
}
