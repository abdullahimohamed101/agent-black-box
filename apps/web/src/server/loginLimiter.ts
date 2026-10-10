/**
 * Sign-in rate limit for the web server (D15). The API never sees client addresses, so the per-client bucket lives
 * here. An address is only usable when a trusted proxy supplied it (`ABB_TRUST_PROXY=1`); Next keeps a
 * client-supplied `X-Forwarded-For`, so without that setting there is no client identity at all and nothing is
 * refused here. There is deliberately no shared bucket over everyone: any pool one anonymous client can drain is a
 * sign-in outage for every other person (security review F1). Only sign-in *starts* are limited; a callback is
 * bound to the browser's own login cookie and is never refused by this limiter. The map is bounded.
 */
export type Clock = () => number;

class Bucket {
  private tokens: number;
  private at: number;
  constructor(
    private readonly perMinute: number,
    now: number,
  ) {
    this.tokens = perMinute;
    this.at = now;
  }
  /** Seconds to wait when empty, else null (a token was taken). */
  take(now: number): number | null {
    this.tokens = Math.min(
      this.perMinute,
      this.tokens + ((now - this.at) / 60_000) * this.perMinute,
    );
    this.at = now;
    if (this.tokens >= 1) {
      this.tokens -= 1;
      return null;
    }
    return Math.max(1, Math.ceil(((1 - this.tokens) * 60) / this.perMinute));
  }
}

export class LoginLimiter {
  private readonly clients = new Map<string, Bucket>();
  constructor(
    private readonly perClientPerMinute = 10,
    private readonly maxClients = 10_000,
    private readonly now: Clock = Date.now,
  ) {}

  /** Null when allowed, else the seconds to wait. A null client (no trustworthy address) is never limited here. */
  acquire(client: string | null): number | null {
    if (client === null) return null;
    const now = this.now();
    let bucket = this.clients.get(client);
    if (!bucket) {
      if (this.clients.size >= this.maxClients) {
        // Oldest first: forgetting a client only gives it a fresh bucket.
        const oldest = this.clients.keys().next().value;
        if (oldest !== undefined) this.clients.delete(oldest);
      }
      bucket = new Bucket(this.perClientPerMinute, now);
      this.clients.set(client, bucket);
    }
    return bucket.take(now);
  }
}

let shared: LoginLimiter | undefined;
export const loginLimiter = (): LoginLimiter => (shared ??= new LoginLimiter());
/** Tests replace the shared limiter. */
export const setLoginLimiter = (limiter: LoginLimiter | undefined) => {
  shared = limiter;
};

/**
 * The client address, only when `ABB_TRUST_PROXY=1` says a proxy we run sets `X-Forwarded-For`. The last entry is
 * the one that proxy appended (or the only one when it overwrites); earlier entries are client-supplied.
 * Otherwise null: nothing the client controls may pick its own bucket.
 */
export function clientKey(headers: Headers): string | null {
  if (process.env.ABB_TRUST_PROXY !== "1") return null;
  const parts = headers.get("x-forwarded-for")?.split(",");
  const last = parts?.[parts.length - 1]?.trim();
  return last && last.length <= 64 ? last : null;
}
