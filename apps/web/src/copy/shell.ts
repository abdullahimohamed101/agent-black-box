/**
 * Strings for the application frame (docs/design/pass-2/05-microcopy-and-states.md, section 1). One module per area so
 * components and tests read the same words. Plain sentences, no jargon, nothing here ever echoes captured content.
 */
export const shell = {
  skip: "Skip to content",
  brand: "Agent Black Box",
  primaryLabel: "Primary",
  nav: {
    runs: "Runs",
    review: "Review",
    analytics: "Analytics",
    experiments: "Experiments",
    settings: "Settings",
  },
  /** Screen-reader suffix after the Review link when requests wait for a decision. */
  reviewWaiting: (n: number) => `, ${n} waiting`,
  menu: "Menu",
  recording: "Recording",
  scope: {
    /** Tooltip on the scope button: the visible text is "workspace / project". */
    hint: "Change workspace or project",
    workspaces: "Workspace",
    projects: "Project",
    allProjects: "All projects",
    noProjects: "No projects yet. They appear once an agent reports here.",
    workspacesList: "Your workspaces",
    projectsList: "Projects",
  },
  search: {
    open: "Search runs",
    openHint: "Search runs (press /)",
    field: "Search all runs",
    placeholder: "Search runs",
    all: "Show all in the runs list",
    searching: "Searching…",
    none: "No runs match this search.",
    unavailable: "Search is unavailable right now. Try again in a moment.",
    busy: "Search is busy. Press Enter to open the runs list, or try again.",
    timeout: "This search took too long. Narrow it and try again.",
    tooLong: (max: number) => `Searches are limited to ${max} characters.`,
    forbidden: (field: string | null) =>
      `Your role cannot search ${field ? `${field}:` : "that field"}, which holds captured content. Metadata search still works.`,
    invalid: (why: string) => `This search could not be understood. ${why}`,
  },
  person: {
    /** Menu button label when there is no signed-in person (fixture data). */
    account: "Account",
    signedInAs: "Signed in as",
    /** The panel of the Account button when the data is fixtures: no person, nothing to sign out of. */
    none: "Fixture data: no one is signed in.",
    role: (role: string) => `Role: ${role}`,
    sessions: "Sessions",
    signOut: "Sign out",
    signOutFailed: "Sign-out failed; you may still be signed in.",
  },
} as const;
