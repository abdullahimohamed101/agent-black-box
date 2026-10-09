/**
 * Sign-in rate limit for the web server (D15). The API never sees client addresses, so the per-client bucket lives
 * here, plus a per-instance bucket over everyone: Next keeps a client-supplied `X-Forwarded-For` when one is
 * present, so an address is a hint, not proof. The global bucket is what bounds a client that rotates addresses.
 * Both maps are bounded; the API keeps its own global backstop.
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
  private readonly everyone: Bucket;
  constructor(
    private readonly perClientPerMinute = 10,
    globalPerMinute = 120,
    private readonly maxClients = 10_000,
    private readonly now: Clock = Date.now,
  ) {
    this.everyone = new Bucket(globalPerMinute, now());
  }

  /** Null when allowed, else the seconds to wait. */
  acquire(client: string): number | null {
    const now = this.now();
    let bucket = this.clients.get(client);
    if (!bucket) {
      if (this.clients.size >= this.maxClients) {
        // Oldest first: forgetting a client only gives it a fresh bucket, and the global bucket still caps the total.
        const oldest = this.clients.keys().next().value;
        if (oldest !== undefined) this.clients.delete(oldest);
      }
      bucket = new Bucket(this.perClientPerMinute, now);
      this.clients.set(client, bucket);
    }
    const mine = bucket.take(now);
    if (mine !== null) return mine;
    return this.everyone.take(now);
  }
}

let shared: LoginLimiter | undefined;
export const loginLimiter = (): LoginLimiter => (shared ??= new LoginLimiter());
/** Tests replace the shared limiter. */
export const setLoginLimiter = (limiter: LoginLimiter | undefined) => {
  shared = limiter;
};

/** The address Next saw, as best it can be known; `unknown` shares one bucket (strict, never lenient). */
export function clientKey(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for");
  const first = forwarded?.split(",")[0]?.trim();
  return first && first.length <= 64 ? first : "unknown";
}
