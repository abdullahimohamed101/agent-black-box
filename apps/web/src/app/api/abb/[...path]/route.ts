import { readThrough } from "@/server/upstream";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(request: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  return readThrough(path, new URL(request.url).searchParams, {
    method: request.method,
    headers: request.headers,
    body: request.body,
    signal: request.signal,
  });
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
