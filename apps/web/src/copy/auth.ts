/** Strings for the pages outside the shell: sign-in, invitation, no workspaces, unavailable (05-microcopy-and-states.md section 2). */
export const auth = {
  loginTitle: "Sign in to see what your agents did",
  signIn: "Sign in",
  aside: "You sign in with your organization's identity provider.",
  fixtureNote: "Fixture mode: no sign-in is needed.",
  fixtureContinue: "Continue",
  /** The fixed sentences of `/login?error=`. The query value is never shown (it is attacker-controllable text). */
  errors: {
    login_failed: "Sign-in did not complete. Try again.",
    rate_limited: "Too many sign-in attempts. Wait a minute and try again.",
    unavailable: "The sign-in service is unavailable. Try again shortly.",
    not_configured: "Sign-in is not configured on this server.",
    no_access: "No access yet. Ask a workspace owner to invite you.",
    logout_failed: "Signing out failed; your session may still be active.",
  },
  invite: {
    title: "Join a workspace",
    reading: "Checking your invitation…",
    unavailable: "The service is unavailable. Reload to try again.",
    noToken:
      "This page needs an invitation link from a workspace owner. Open the full link you were sent.",
    waiting: "An invitation to a workspace is waiting for you.",
    signedOutHelp: "Sign in with the email address the invitation was sent to, then accept it.",
    signedInAs: "You are signed in as",
    emailRule: "The invitation only works for the email address it was sent to.",
    accept: "Accept invitation",
    accepting: "Accepting…",
    failed: "The invitation could not be accepted.",
  },
  home: {
    fixtureTitle: "Agent Black Box",
    fixtureLede: "Flight recorder for AI agents.",
    fixtureOpen: "Open your runs →",
    unavailableSuffix: "Try again shortly.",
    noWorkspacesTitle: "No workspaces yet",
    noWorkspaces:
      "You are signed in, but you are not a member of any workspace. Ask a workspace owner for an invitation link, then open it.",
  },
  workspaceUnavailable: { title: "Workspace unavailable", suffix: "Reload to try again." },
  notFound: {
    title: "This page isn't here",
    body: "The link may be old, or the run or project may have been removed.",
    action: "Go to your runs",
  },
  error: {
    title: "Something broke on this page",
    body: (ref: string | undefined) =>
      `Nothing you recorded was lost. Try again, and if it keeps happening tell whoever runs this workspace${ref ? ` (reference ${ref})` : ""}.`,
    retry: "Try again",
    action: "Go to your runs",
  },
} as const;

export type LoginErrorKey = keyof typeof auth.errors;
