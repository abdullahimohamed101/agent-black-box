/** Strings for Review: the Approvals and Security findings tabs (docs/design/pass-2/05-microcopy-and-states.md section 7). */
export const review = {
  title: "Review",
  tabsLabel: "Review sections",
  tabs: { approvals: "Approvals", security: "Security findings" },
  waiting: (n: number) => `${n} waiting`,
  approvals: {
    stateLabel: "Approval state",
    caption: "Approval requests, newest first",
    heads: {
      request: "Request",
      risk: "Risk",
      state: "State",
      requested: "Requested",
      left: "Time left",
    },
    expired: "Expired",
    loading: "Loading approvals…",
    loaded: (n: number, end: boolean) => `${n} request${n === 1 ? "" : "s"} loaded${end ? " (end of list)" : ""}`,
    more: "Load more",
    loadingMore: "Loading…",
    foot: "An approval authorises one action, once. Policies apply to agents that ask through the SDK guard.",
    forbiddenWhat: "approvals",
    empty: {
      pending: {
        title: "Nothing is waiting for you",
        body: "When an agent asks a person to approve an action, the request appears here.",
      },
      decided: {
        title: "No decided requests yet",
        body: "Approved, denied, expired and cancelled requests are listed here.",
      },
      all: {
        title: "No approval requests yet",
        body: "Policies that require approval create a request when an agent asks to act.",
      },
    },
  },
  security: {
    caption: "Security findings, newest run first",
    heads: { severity: "Severity", finding: "Finding", run: "Run", when: "When" },
    loading: "Loading security findings…",
    loaded: (n: number, end: boolean) => `${n} finding${n === 1 ? "" : "s"} loaded${end ? " (end of list)" : ""}`,
    forbiddenWhat: "security findings",
    cleanTitle: "Nothing suspicious found",
    cleanBody: "No security findings in this workspace. These detectors are on:",
    noneTitle: "No findings match",
    noneBody: "Try clearing a filter or widening the time range.",
    clear: "Clear filters",
    more: "Load more",
    loadingMore: "Loading…",
    narrow: "Narrow results",
  },
} as const;
