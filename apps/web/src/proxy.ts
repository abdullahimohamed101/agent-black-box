import { NextResponse, type NextRequest } from "next/server";
import { loginRedirect } from "@/server/gate";

// Next 16's request interceptor (formerly middleware). Only workspace pages are gated here; /invite handles its own
// sign-in prompt so the invitation token in its URL fragment survives.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const to = loginRedirect(pathname, search, request.headers.get("cookie"));
  if (!to) return NextResponse.next();
  return new NextResponse(null, {
    status: 307,
    headers: { location: to, "cache-control": "no-store" },
  });
}

export const config = { matcher: ["/w/:path*"] };
