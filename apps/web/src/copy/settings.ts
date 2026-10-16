/**
 * Strings for the Settings pattern, every settings tab and Alerts (docs/design/pass-2/05-microcopy-and-states.md sections 8
 * and 9). The same words are used on every tab so a person learns them once. Nothing here echoes captured content.
 */

/** Which permission each tab needs (the API still decides what a request may do); `null` means every signed-in person. */
export type SettingsSlug =
  | "members"
  | "sessions"
  | "api-keys"
  | "policies"
  | "security"
  | "quotas"
  | "retention"
  | "pricing"
  | "alerts"
  | "audit";

export const settingsNav = {
  label: "Settings sections",
  /** The word before the tab name on the button that opens the list below 900 px. */
  sectionPrefix: "Section",
  groups: [
    {
      id: "people",
      label: "People",
      tabs: [
        { slug: "members", label: "Members", needs: "member.read" },
        { slug: "sessions", label: "Sessions", needs: null },
      ],
    },
    {
      id: "access",
      label: "Access",
      tabs: [
        { slug: "api-keys", label: "API keys", needs: "api_key.read" },
        { slug: "policies", label: "Policies", needs: "policy.read" },
        { slug: "security", label: "Capture and detectors", needs: "project.write" },
      ],
    },
    {
      id: "limits",
      label: "Limits",
      tabs: [
        { slug: "quotas", label: "Quotas", needs: "project.read" },
        { slug: "retention", label: "Retention", needs: "retention.read" },
        { slug: "pricing", label: "Pricing", needs: "pricing.read" },
      ],
    },
    {
      id: "notify",
      label: "Notify",
      tabs: [{ slug: "alerts", label: "Alerts", needs: "alert.read" }],
    },
    {
      id: "record",
      label: "Record",
      tabs: [{ slug: "audit", label: "Audit log", needs: "audit.read" }],
    },
  ],
} as const satisfies {
  label: string;
  sectionPrefix: string;
  groups: readonly {
    id: string;
    label: string;
    tabs: readonly { slug: SettingsSlug; label: string; needs: string | null }[];
  }[];
};

export const settings = {
  title: "Settings",
  back: { label: "Settings", name: "Back to settings" },
  forbidden: {
    title: "Not available for your role",
    body: (what: string) => `Your role in this workspace does not include ${what}.`,
  },
  readOnly: "Your role can read this but not change it. Owners and admins can.",
  saved: "Saved.",
  saving: "Saving…",
  cancel: "Cancel",
  denied: "Your role does not allow this.",
  failed: "The request failed.",
  request: (id: string) => `request ${id}`,
  /** The mark before a field's error sentence. Ink, never red; the word makes it more than a colour. */
  errorWord: "Error:",
  confirm: {
    remove: { first: "Remove", second: "Confirm remove" },
    revoke: { first: "Revoke", second: "Confirm revoke" },
  },
  loading: (what: string) => `Loading ${what}…`,

  members: {
    lede: "Who can open this workspace, and what they may do.",
    tableCaption: "Workspace members",
    heads: { member: "Member", role: "Role", joined: "Joined", actions: "Actions" },
    you: "you",
    empty: "No members",
    invitations: {
      title: "Invitations",
      lede: "An invitation works once, for the email address it was sent to. The link is shown once.",
      caption: "Open invitations",
      heads: { email: "Email", role: "Role", expires: "Expires", actions: "Actions" },
      empty: "No open invitations",
      email: "Email",
      role: "Role",
      create: "Create invitation",
      creating: "Inviting…",
      done: "Done",
    },
  },
  apiKeys: {
    lede: "Keys let SDKs and tools send events and read runs. A token is shown once, when the key is created.",
    listTitle: "Keys",
    createTitle: "Create a key",
    createLede: "Pick the smallest set of scopes the tool needs.",
    caption: "API keys",
    heads: {
      name: "Name",
      keyId: "Key id",
      scopes: "Scopes",
      project: "Project",
      lastUsed: "Last used",
      status: "Status",
      actions: "Actions",
    },
    unnamed: "unnamed",
    emptyTitle: "No API keys",
    emptyBody: "Create one to connect an agent.",
    create: "Create key",
    creating: "Creating…",
    stored: "I have stored it",
  },
  audit: {
    lede: "Administrative actions and refused attempts, newest first.",
    caption: "Audit log",
    heads: {
      when: "When",
      actor: "Actor",
      action: "Action",
      outcome: "Outcome",
      resource: "Resource",
      details: "Details",
    },
    empty: "Nothing recorded yet",
    more: "Load more",
    loadingMore: "Loading…",
  },
  pricing: {
    lede: "Prices per million tokens used to cost LLM calls. An override replaces the built-in price for matching models from its start time; past runs change only after a rebuild.",
    pricesTitle: "Prices",
    addTitle: "Add an override",
    applyTitle: "Apply to past runs",
    applyLede: "Recomputes the cost of existing runs with the prices above.",
    empty: "No prices",
  },
  sessions: {
    lede: "The browsers and devices where you are signed in. Ending a session signs that device out at once. Each change is written to the audit log of your workspaces.",
    listTitle: "Your sessions",
  },
  quotas: {
    ledeLead:
      "What one project may store. At a limit, new items are refused with a clear code; what the project already has keeps working. Changes to a limit are written to the ",
    usageTitle: "Usage and limits",
    limit: "Limit",
    near: "Near limit",
  },
  retention: {
    ledeLead:
      "How long this workspace keeps its data. Older data is removed by a background job, a run at a time, and every change to these periods is written to the ",
  },
  security: {
    ledeLead:
      "What the server stores, which detectors report and which hosts an agent may contact, for one project. Every change is written to the ",
  },
  policies: {
    lede: "Rules for what an agent may do. The first rule that matches decides; anything that matches no rule is allowed.",
  },
  alerts: {
    title: "Alerts",
    lede: "Tell a person or a channel when a run fails, a security finding appears, an approval needs attention or spend goes over a limit.",
    tabsLabel: "Alert sections",
    tabs: { rules: "Rules", destinations: "Destinations", history: "History" },
    foot: "Alerts are notifications that are sent as soon as possible and may arrive more than once. They carry names, ids and counts, never what an agent read, wrote or ran.",
  },
} as const;
