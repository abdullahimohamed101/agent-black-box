import { readThrough } from "@/server/upstream";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await ctx.params;
  return readThrough(path, new URL(request.url).searchParams, {
    lastEventId: request.headers.get("last-event-id"),
    signal: request.signal,
  });
}
