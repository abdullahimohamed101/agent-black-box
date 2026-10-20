/** Shared loading, empty and error words (05-microcopy-and-states.md section 4). Specific, no jargon, never echoing content. */
export const states = {
  loading: (what: string) => `${what}…`,
  updating: "Updating…",
  error: {
    generic: "Could not load this data",
    notFound: "Not found",
    tryAgain: "Try again",
    request: (id: string) => `request ${id}`,
    unexpected: "Unexpected error",
    runGone:
      "This run no longer exists. It may have been removed by the retention policy, or the link may be wrong.",
    forbidden: {
      title: "Not available for your role",
      body: "Ask a workspace owner if you need access.",
    },
    signedOut: {
      title: "You have been signed out",
      body: "Sign in again to pick up where you left off.",
      action: "Sign in",
    },
    rateLimited: {
      title: "Too many requests",
      soon: "Wait a moment and try again.",
      after: (s: number) => `Wait ${s} second${s === 1 ? "" : "s"} and try again.`,
    },
  },
} as const;
