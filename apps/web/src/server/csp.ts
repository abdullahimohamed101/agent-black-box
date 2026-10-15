/**
 * The web app's Content-Security-Policy and the other edge headers (Phase 19a, ADR-155). Pure functions so the policy
 * is unit-tested; `src/proxy.ts` applies them per request (the nonce must be fresh for every response).
 */

/** What an API route (the relay, auth redirects, health) may do: it renders nothing, so it may load nothing. */
export const ROUTE_CSP =
  "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

export const HSTS = "max-age=31536000; includeSubDomains";

export interface CspOptions {
  nonce: string;
  /** `next dev` only: React rebuilds server stacks with eval. Never in a production build. */
  dev?: boolean;
  /** The deployment is served over https (ABB_WEB_ORIGIN is an https origin). */
  https?: boolean;
}

export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text);
}

/**
 * Pages. Scripts run only with this response's nonce (`strict-dynamic` extends that trust to the chunks the
 * bootstrap loads; `'self'` is only the fallback for browsers without it); there is no 'unsafe-inline' for scripts.
 * Styles: stylesheets are our own files; the one inline allowance is `style-src-attr`, for React `style={{}}`
 * attributes that set a computed width or offset (a style attribute cannot run code). Everything else is same-origin.
 */
export function pageCsp({ nonce, dev = false, https = false }: CspOptions): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'none'",
    "media-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ];
  if (https) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

/** Headers for every response regardless of route (also set statically in next.config.ts for assets). */
export const BASE_HEADERS: Record<string, string> = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Permissions-Policy":
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), interest-cohort=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
};
