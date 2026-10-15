import type { components } from "@/lib/api/schema";

/**
 * The session routes of the fixture API (Phase 19a, KI-068): the signed-in person's own sessions with in-memory state, so
 * the Sessions page can be exercised without an API. The first session is "this one" and cannot be revoked; the rules
 * (a session that is not yours or is gone is the same 404) mirror the API's.
 */
type Session = components["schemas"]["SessionOut"];
type Reply = { status: number; body: unknown };

const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const SEED: Session[] = [
  {
    id: ID(1),
    device: "Firefox on Linux",
    created_at: "2026-10-07T08:00:00Z",
    last_seen_at: "2026-10-07T09:25:00Z",
    expires_at: "2026-10-14T08:00:00Z",
    idle_expires_at: "2026-10-08T09:25:00Z",
    current: true,
  },
  {
    id: ID(2),
    device: "Safari on iOS",
    created_at: "2026-10-05T18:10:00Z",
    last_seen_at: "2026-10-06T07:40:00Z",
    expires_at: "2026-10-12T18:10:00Z",
    idle_expires_at: "2026-10-07T07:40:00Z",
    current: false,
  },
  {
    id: ID(3),
    device: null,
    created_at: "2026-10-01T10:00:00Z",
    last_seen_at: "2026-10-02T10:30:00Z",
    expires_at: "2026-10-08T10:00:00Z",
    idle_expires_at: "2026-10-03T10:30:00Z",
    current: false,
  },
];

let live: Session[] = SEED.map((s) => ({ ...s }));
export const resetFixtureSessions = (): void => {
  live = SEED.map((s) => ({ ...s }));
};

const err = (status: number, code: string, message: string, category: string): Reply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details: {} },
  },
});

/** `GET /v1/auth/sessions`, `DELETE /v1/auth/sessions/{id}` and `POST /v1/auth/sessions/revoke-others`; null for other paths. */
export function fixtureSessions(method: string, segments: string[]): Reply | null {
  const [v1, auth, sessions, tail] = segments;
  if (v1 !== "v1" || auth !== "auth" || sessions !== "sessions") return null;
  if (segments.length === 3 && method === "GET") return { status: 200, body: { items: live } };
  if (segments.length === 4 && tail === "revoke-others" && method === "POST") {
    const kept = live.filter((s) => s.current);
    const revoked = live.length - kept.length;
    live = kept;
    return { status: 200, body: { revoked } };
  }
  if (segments.length === 4 && method === "DELETE") {
    const found = live.find((s) => s.id === tail);
    if (!found) return err(404, "SESSION_NOT_FOUND", "Session not found.", "NOT_FOUND");
    if (found.current)
      return err(
        409,
        "CURRENT_SESSION",
        "This is the session you are using. Sign out to end it.",
        "CONFLICT",
      );
    live = live.filter((s) => s.id !== tail);
    return { status: 204, body: null };
  }
  return null;
}
