import { NextResponse, type NextRequest } from "next/server";
import { webOrigin } from "@/server/config";
import { loginRedirect } from "@/server/gate";

// Next 16's request interceptor (formerly middleware). Only workspace pages are gated here; /invite handles its own
// sign-in prompt so the invitation token in its URL fragment survives.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const to = loginRedirect(pathname, search, request.headers.get("cookie"));
  if (!to) return NextResponse.next();
  // Next needs an absolute URL here; the configured origin beats the Host header a client could choose.
  const res = NextResponse.redirect(new URL(to, webOrigin() ?? request.url), 307);
  res.headers.set("cache-control", "no-store");
  return res;
}

export const config = { matcher: ["/w/:path*"] };
